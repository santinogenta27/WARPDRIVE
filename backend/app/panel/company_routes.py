"""Panel, pantallas del hito 6 (decisión 026): alta de empresas, onboarding sin comandos,
revisión de temas y acciones, y conexión con el sistema de la empresa.

Todas las rutas entran por `enter` (app/panel/routes.py): la empresa sale de la sesión y cada
consulta corre dentro de `tenant_scope`, así que un id de otra empresa responde 404 igual que
uno que no existe. Los POST exigen el token anti-CSRF.
"""

import hmac
import re
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import i18n
from app.agent import supervisor as answer_supervisor
from app.agent import voice as brand_voice
from app.cases import supervision
from app.channels.web import widget
from app.channels.whatsapp import selfservice as whatsapp
from app.companies import connection as system
from app.companies import delete as removal
from app.companies import deploy, export, onboarding, review, rules
from app.companies.create import CompanyError, chat_slug, create_company
from app.connectors import platforms, shopify, shopify_auth
from app.demo import behind
from app.onboarding import ingest as ingestion
from app.onboarding.ingest import Upload
from app.panel import agent_forms as forms
from app.panel import auth, english, guides
from app.panel.html import esc, local_time, page, toast_text
from app.panel.labels import tool_label
from app.panel.routes import Ctx, Deps, Form, PanelDeps, _forbidden, _not_found, _user_id, enter
from app.sales import upsell

router = APIRouter(prefix="/panel", include_in_schema=False)

MAX_BODY = onboarding.MAX_UPLOAD_BYTES + 1024 * 1024
KINDS = {
    "whatsapp_export": "Chats de WhatsApp",
    "helpdesk_export": "Tickets de helpdesk",
    "document": "Documento",
    "url": "Página web",
}
STAGE_STATUS = {
    "pendiente": "Pendiente",
    "en_curso": "En curso",
    "hecha": "Hecha",
    "fallo": "Falló",
}


# -- formularios con archivos ----------------------------------------------------------------
def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], list[Upload]]:
    """Campos de texto y archivos de un `multipart/form-data` (sin dependencias: decisión 022).
    Los bytes de cada archivo salen tal cual llegaron."""
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if match is None:
        return {}, []
    delimiter = b"\r\n--" + match.group(1).encode()
    fields: dict[str, str] = {}
    files: list[Upload] = []
    for chunk in (b"\r\n" + body).split(delimiter)[1:]:
        if chunk.startswith(b"--"):
            break
        head, separator, data = chunk.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = head.decode("utf-8", errors="replace")
        name = re.search(r'name="([^"]*)"', headers)
        filename = re.search(r'filename="([^"]*)"', headers)
        if name is None:
            continue
        if filename is not None:
            if filename.group(1):  # un campo de archivo vacío llega con filename=""
                files.append(Upload(filename.group(1).replace("\\", "/").split("/")[-1], data))
        else:
            fields[name.group(1)] = data.decode("utf-8", errors="replace")
    return fields, files


class Multipart:
    def __init__(self, fields: dict[str, str], files: list[Upload], too_big: bool) -> None:
        self.fields, self.files, self.too_big = fields, files, too_big


async def multipart_data(request: Request) -> Multipart:
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_BODY:
        return Multipart({}, [], True)
    body = await request.body()
    if len(body) > MAX_BODY:
        return Multipart({}, [], True)
    fields, files = parse_multipart(request.headers.get("content-type", ""), body)
    return Multipart(fields, files, False)


Files = Annotated[Multipart, Depends(multipart_data)]


def _notice(request: Request) -> str:
    """Mensaje del último cambio (`?ok=` o `?error=` en la URL del redirect)."""
    ok, error = request.query_params.get("ok"), request.query_params.get("error")
    if error:
        # Una línea por renglón: cada una se traduce sola (qué pasó, qué hacer).
        lines = "".join(f"<div>{esc(line)}</div>" for line in error[:600].split("\n") if line)
        return f'<div class="card alert" role="alert">{lines}</div>'
    # `aviso`: algo que no se pudo hacer aunque el resto sí (un PDF escaneado en una carga).
    warning = request.query_params.get("aviso")
    shown = toast_text(ok[:400]) if ok else ""
    return shown + (f'<div class="card alert">{esc(warning[:1200])}</div>' if warning else "")


def _back(path: str, *, ok: str = "", error: str = "", warning: str = "") -> RedirectResponse:
    from urllib.parse import quote

    query = f"?error={quote(error)}" if error else f"?ok={quote(ok)}" if ok else ""
    if warning and not error:
        query += f"{'&' if query else '?'}aviso={quote(warning)}"
    return RedirectResponse(f"/panel/{path}{query}", status_code=303)


def _csrf(ctx: Ctx) -> str:
    return f'<input type="hidden" name="csrf" value="{ctx.csrf}">'


# -- empresas (solo el operador de la plataforma) --------------------------------------------
OPERATOR_ACTIONS = {"company_created": "Creó la empresa", "company_deleted": "Borró la empresa"}
TABLE_LABELS = {
    "cases": "casos",
    "case_messages": "mensajes de casos",
    "conversations": "conversaciones del historial",
    "messages": "mensajes del historial",
    "documents": "documentos",
    "users": "usuarios",
    "contacts": "contactos",
    "audit_events": "eventos de auditoría",
    "tools": "acciones",
    "case_types": "temas",
}


def _deleted_note(detail: Any) -> str:
    rows = (detail or {}).get("filas") if isinstance(detail, dict) else None
    if not rows:
        return ""
    return f' <span class="muted small">· {sum(rows.values())} filas en {len(rows)} tablas</span>'


def _companies(deps: PanelDeps) -> list[tuple[Any, ...]]:
    # Lista de empresas: consulta fuera de un tenant, solo para el operador de la plataforma.
    with deps.connection() as conn:
        return conn.execute(
            "select t.id, t.name, t.created_at, (select string_agg(u.email, ', ') from users u"
            " where u.tenant_id = t.id and u.password_hash is not null),"
            " (select c.phone_number_id from channels c where c.tenant_id = t.id"
            "  and c.kind = 'web' and c.status = 'active' order by c.created_at limit 1)"
            " from tenants t where t.status = 'active' and exists (select 1 from users u"
            "  where u.tenant_id = t.id and u.password_hash is not null)"
            " order by t.created_at desc limit 100"
        ).fetchall()


def _companies_page(ctx: Ctx, deps: PanelDeps, notice: str, created: str = "") -> HTMLResponse:
    rows = "".join(
        f"<tr><td><b>{esc(name)}</b></td><td>{esc(users)}</td>"
        f'<td class="mono">/c/{esc(str(address or "")[4:])}</td>'
        f"<td>{local_time(created_at, '%d/%m/%Y')}</td>"
        f'<td><a href="/panel/empresas/{tenant_id}/exportar">Exportar</a> · '
        f'<a href="/panel/empresas/{tenant_id}/borrar">Borrar…</a></td></tr>'
        for tenant_id, name, created_at, users, address in _companies(deps)
    )
    table = (
        f"<table><tr><th>Empresa</th><th>Usuario</th><th>Chat de prueba</th><th>Alta</th>"
        f"<th></th></tr>{rows}</table>"
        if rows
        else '<p class="muted">Todavía no creaste ninguna empresa.</p>'
    )
    with deps.connection() as conn:
        actions = removal.recent(conn)
    log_rows = "".join(
        f"<tr><td>{local_time(at, '%d/%m/%Y %H:%M')}</td><td>{esc(operator)}</td>"
        f"<td>{esc(OPERATOR_ACTIONS.get(action, action))} {esc(company_name)}"
        f"{_deleted_note(detail)}</td></tr>"
        for at, operator, action, company_name, detail in actions
    )
    log = (
        f"<table><tr><th>Fecha</th><th>Operador</th><th>Acción</th></tr>{log_rows}</table>"
        if log_rows
        else '<p class="muted">Todavía no hay acciones registradas.</p>'
    )
    body = f"""<h1>Empresas</h1><p class="lead">Cada empresa tiene su usuario, sus datos y su
agente, aislados de las demás. Nace vacía: todo lo que su agente sabe sale de su propio
onboarding.</p>{notice}{created}
<div class="card"><h2 style="margin-top:0">Crear una empresa</h2>
<form method="post" action="/panel/operator/companies">{_csrf(ctx)}
<label for="nombre">Nombre de la empresa</label>
<input id="nombre" name="nombre" required maxlength="80">
<label for="email">Email del usuario</label>
<input id="email" name="email" type="email" required autocomplete="off">
<label for="clave">Clave del usuario (al menos {auth.MIN_PASSWORD} caracteres)</label>
<input id="clave" name="clave" type="password" required minlength="{auth.MIN_PASSWORD}"
 autocomplete="new-password">
<p><button class="primary" type="submit">Crear empresa</button></p></form></div>
<div class="card"><h2 style="margin-top:0">Empresas creadas</h2>{table}</div>
<div class="card"><h2 style="margin-top:0">Registro del operador</h2>{log}
<p class="muted small">Altas y borrados de empresas. No se puede modificar ni borrar.</p></div>"""
    return HTMLResponse(page("Empresas", body, active="operator/companies", session=ctx.session))


@router.get("/operator/companies", response_class=HTMLResponse)
def companies_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.session.admin:
        return _forbidden("Solo el operador de la plataforma puede crear empresas.")
    return _companies_page(ctx, deps, _notice(request))


@router.post("/empresas")
@router.post("/operator/companies")
def create_company_route(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.session.admin:
        return _forbidden("Solo el operador de la plataforma puede crear empresas.")
    try:
        with deps.connection() as conn:
            company = create_company(
                conn, form.get("nombre", ""), form.get("email", ""), form.get("clave", "")
            )
            removal.record(
                conn, ctx.session.email, "company_created", company.tenant_id, company.name
            )
    except CompanyError as error:
        return _back("operator/companies", error=str(error))
    created = f"""<div class="card okmsg"><b>{esc(company.name)} quedó creada.</b>
<p>Para entrar a su panel: cerrá esta sesión e ingresá con el email y la clave que cargaste.
La clave no se vuelve a mostrar.</p>
<p>Su chat de prueba: <span class="mono">/c/{esc(company.chat_slug)}</span></p></div>"""
    return _companies_page(ctx, deps, "", created)


# -- onboarding ------------------------------------------------------------------------------
@router.post("/onboarding/indexados/{document_id}/borrar")
def delete_indexed(document_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = ingestion.delete_document(conn, ctx.tenant_id, document_id)
    if not found:
        return _not_found("Ese documento", ctx.session)
    return _back("knowledge", ok="Documento borrado: el agente ya no lo usa.")


@router.post("/onboarding/sitio")
async def read_site(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    url = form.get("sitio", "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return _back("knowledge", error="La dirección tiene que empezar con https://.")
    result = await run_in_threadpool(_ingest, deps, ctx.tenant_id, [Upload(url, b"")])
    if result.unreadable:
        return _back("knowledge", error=" ".join(result.unreadable))
    return _back("knowledge", ok=f"Se leyeron {result.documents} páginas del sitio.")


def _pending_html(ctx: Ctx, waiting: list[ingestion.PendingDocument]) -> str:
    """Documentos que parecen tener datos de personas: no se indexan hasta que la empresa
    confirma. Se muestra el motivo, nunca los datos."""
    if not waiting:
        return ""
    items = "".join(
        f"""<li class="row" style="padding:8px 0"><span class="grow"><b>{esc(doc.name)}</b>
<div class="muted small">Tiene {esc(doc.reason)}. Subido el {local_time(doc.created_at)}.</div>
</span><form method="post" action="/panel/onboarding/documentos/{doc.id}/indexar">{_csrf(ctx)}
<button type="submit">Indexar igual</button></form>
<form method="post" action="/panel/onboarding/documentos/{doc.id}/descartar">{_csrf(ctx)}
<button class="primary" type="submit">Descartar</button></form></li>"""
        for doc in waiting
    )
    return f"""<div class="card alert" style="color:var(--ink)">
<h2 style="margin-top:0">Documentos que esperan tu confirmación</h2>
<p>Estos documentos parecen tener datos de personas y todavía <b>no se indexaron</b>. El
agente responde con lo que dicen tus documentos: si uno trae emails o teléfonos de clientes,
podría mostrárselos a otro cliente.</p>
<ul style="list-style:none;padding:0;margin:0">{items}</ul>
<p class="muted small">Si es una política con los contactos de tu empresa, indexalo. Si es una
lista de clientes o de empleados, descartalo. Los números de tarjeta y los DNI ya se
reemplazaron.</p></div>"""


def _ingest(deps: PanelDeps, tenant_id: uuid.UUID, files: list[Upload]) -> Any:
    with deps.connection() as conn:
        return onboarding.add_sources(conn, tenant_id, files, deps.embedder(conn))


@router.post("/onboarding/fuentes")
async def upload_sources(request: Request, data: Files, deps: Deps) -> Response:
    if data.too_big:
        return _back("knowledge", error="El envío supera los 50 MB.")
    ctx = enter(request, deps, data.fields)
    if isinstance(ctx, Response):
        return ctx
    if not data.files:
        return _back("knowledge", error="Elegí al menos un archivo.")
    try:
        result = await run_in_threadpool(_ingest, deps, ctx.tenant_id, data.files)
    except onboarding.OnboardingError as error:
        return _back("knowledge", error=str(error))
    summary = (
        f"Subiste {result.sources} archivos: {result.conversations} conversaciones y "
        f"{result.documents} documentos."
    )
    if result.skipped:
        summary += f" {result.skipped} ya estaban subidos."
    if result.replaced:
        summary += f" {result.replaced} reemplazaron a uno anterior con el mismo nombre."
    # Un documento sin texto (PDF escaneado) se avisa acá: el resto de la carga ya quedó.
    waiting = [
        f"{note} Parece tener datos de personas: no se indexó. Confirmalo o descartalo acá abajo."
        for note in result.held
    ]
    warning = " ".join([*result.unreadable, *waiting])
    if not result.sources and not result.skipped:
        return _back("knowledge", warning=warning) if warning else _back("knowledge", ok=summary)
    return _back("knowledge", ok=summary, warning=warning)


@router.post("/onboarding/documentos/{document_id}/{action}")
async def decide_pending_document(
    document_id: uuid.UUID, action: str, request: Request, form: Form, deps: Deps
) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if action not in ("indexar", "descartar"):
        return _not_found("Esa acción", ctx.session)

    def decide() -> str | None:
        with deps.connection() as conn:
            if action == "indexar":
                return ingestion.confirm_pending(
                    conn, ctx.tenant_id, document_id, deps.embedder(conn)
                )
            return ingestion.discard_pending(conn, ctx.tenant_id, document_id)

    name = await run_in_threadpool(decide)
    if name is None:
        return _not_found("Ese documento", ctx.session)
    done = "quedó indexado" if action == "indexar" else "se descartó: no quedó nada guardado"
    return _back("knowledge", ok=f"{name} {done}.")


@router.post("/onboarding/nombres")
def save_business_names(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        onboarding.set_business_names(conn, ctx.tenant_id, form.get("nombres", "").split(","))
    return _back("knowledge", ok="Nombres guardados.")


@router.post("/onboarding/iniciar")
def start_onboarding(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            user_id = _user_id(conn, ctx.tenant_id, ctx.session.email)
            onboarding.start_run(conn, ctx.tenant_id, user_id)
    except onboarding.OnboardingError as error:
        return _back("knowledge", error=str(error))
    deps.kick_worker()  # con la corrida ya confirmada en la cola
    return _back("knowledge", ok="El análisis empezó.")


ONLY_OPERATOR_DELETES = "Solo el operador de la plataforma puede borrar una empresa."


@router.get("/empresas/{tenant_id}/exportar")
def export_company_route(tenant_id: uuid.UUID, request: Request, deps: Deps) -> Response:
    """Los datos de la empresa en un .zip, sin secretos (decisión 055). Solo el operador."""
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.session.admin:
        return _forbidden("Solo el operador de la plataforma puede exportar una empresa.")
    try:
        with deps.connection() as conn:
            data = export.export_company(conn, tenant_id, operator=ctx.session.email)
    except ValueError:
        return _not_found("Esa empresa", ctx.session)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="empresa-{tenant_id}.zip"'},
    )


@router.get("/empresas/{tenant_id}/borrar", response_class=HTMLResponse)
def delete_company_page(tenant_id: uuid.UUID, request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.session.admin:
        return _forbidden(ONLY_OPERATOR_DELETES)
    with deps.connection() as conn:
        found = removal.company(conn, tenant_id)
        rows = removal.counts(conn, tenant_id) if found else {}
    if found is None:
        return _not_found("Esa empresa", ctx.session)
    name = found[0]
    known = [(TABLE_LABELS[t], rows[t]) for t in TABLE_LABELS if rows.get(t)]
    other = sum(count for table, count in rows.items() if table not in TABLE_LABELS)
    items = "".join(f"<li><b>{count}</b> {esc(label)}</li>" for label, count in known)
    if other:
        items += f"<li><b>{other}</b> filas más (configuración, uso, sesiones del chat…)</li>"
    body = f"""<p><a href="/panel/operator/companies">← Empresas</a></p>
<div class="card alert" style="max-width:640px;color:var(--ink)">
<h1 style="color:var(--bad)">Borrar {esc(name)}</h1>
<p><b>Esto no se puede deshacer.</b> Se borran todos los datos de la empresa:</p>
<ul>{items or "<li>La empresa todavía no tiene datos.</li>"}</ul>
<p>También se borran sus usuarios, su chat, la conexión con su sistema y sus credenciales. Sus
usuarios no van a poder entrar más. En el registro del operador queda quién la borró y cuándo,
sin datos de la empresa.</p>
<form method="post" action="/panel/empresas/{tenant_id}/borrar"
 data-confirm="Delete this company and all its data? It can't be undone."
 data-confirm-ok="Delete everything">{_csrf(ctx)}
<label for="nombre">Para confirmar, escribí el nombre de la empresa: {esc(name)}</label>
<input id="nombre" name="nombre" required autocomplete="off">
<p class="row"><button class="danger" type="submit">Borrar la empresa y todos sus datos</button>
<a class="btn" href="/panel/operator/companies">Cancelar</a></p></form></div>"""
    return HTMLResponse(
        page("Borrar empresa", body, active="operator/companies", session=ctx.session)
    )


@router.post("/empresas/{tenant_id}/borrar")
def delete_company_route(
    tenant_id: uuid.UUID, request: Request, form: Form, deps: Deps
) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.session.admin:
        return _forbidden(ONLY_OPERATOR_DELETES)
    try:
        with deps.connection() as conn:
            deleted = removal.delete_company(
                conn, tenant_id, operator=ctx.session.email, confirm_name=form.get("nombre", "")
            )
    except removal.DeleteError as error:
        return _back("operator/companies", error=str(error))
    total = sum(deleted.values())
    return _back(
        "operator/companies",
        ok=f"La empresa quedó borrada: {total} filas en {len(deleted)} tablas.",
    )


# -- configuración: temas, acciones, sistema y chat ------------------------------
MODES = {
    "demo": ("resolved", "Sistema de demo (API simulada de Acme)"),
    "real": ("resolved", "Conectado al sistema real"),
    "simulado": ("waiting_approval", "Modo simulado: las acciones no tocan tu sistema"),
    "sin_sistema": ("escalated", "Sin sistema conectado: el agente solo informa"),
}


def _whatsapp_html(ctx: Ctx, request: Request, found: whatsapp.WhatsAppSetup | None) -> str:
    """Canales → WhatsApp (decisión 068): los datos de la app de Meta, el webhook para copiar,
    probar, mensaje de prueba, activar y la guía. En el idioma del panel."""
    en = english.english()

    def t(es: str, en_text: str) -> str:
        return en_text if en else es

    saved = t("Guardado. Dejalo vacío para conservarlo.", "Saved. Leave it empty to keep it.")
    status = ""
    webhook = ""
    actions = ""
    test = ""
    if found is not None:
        active = found.status == "active"
        status = (
            f'<span class="badge {"ok" if active else "warn"}">'
            f"{t('Activo', 'Active') if active else t('Inactivo', 'Inactive')}</span>"
        )
        url = f"{_public_url(request)}/webhooks/whatsapp/{found.webhook_key}"
        webhook = f"""<h3>{t("Webhook para pegar en Meta", "Webhook to paste in Meta")}</h3>
<label>{t("URL de devolución de llamada", "Callback URL")}</label>
<div class="mono small" id="wa-webhook">{esc(url)}</div>
<label>{t("Token de verificación", "Verify token")}</label>
<div class="mono small" id="wa-verify">{esc(found.verify_token or "")}</div>
<p class="muted small">{
            t(
                "Pegalos en tu app de Meta (WhatsApp → Configuración → Webhook) y suscribite al "
                "campo messages.",
                "Paste them in your Meta app (WhatsApp → Configuration → Webhook) and subscribe to "
                "the messages field.",
            )
        }</p>"""
        if found.last_test:
            ok = bool(found.last_test.get("ok"))
            test = (
                f'<div class="card {"okmsg" if ok else "alert"}" style="margin-top:10px">'
                f"<b>{t('La prueba pasó', 'The test passed') if ok else t('La prueba falló', 'The test failed')}."  # noqa: E501
                f"</b> {esc(found.last_test.get('detalle'))}</div>"
            )
        toggle = (
            f'<form method="post" action="/panel/configuracion/whatsapp/desactivar"'
            f' data-confirm="Turn off WhatsApp? Your agent stops answering there.">{_csrf(ctx)}'
            f'<button type="submit">{t("Desactivar", "Deactivate")}</button></form>'
            if active
            else f'<form method="post" action="/panel/configuracion/whatsapp/activar">'
            f'{_csrf(ctx)}<button class="primary" type="submit">{t("Activar", "Activate")}'
            "</button></form>"
        )
        actions = f"""<div class="row">
<form method="post" action="/panel/configuracion/whatsapp/probar">{_csrf(ctx)}
<button type="submit">{t("Probar conexión", "Test connection")}</button></form>{toggle}</div>
{test}<form method="post" action="/panel/configuracion/whatsapp/mensaje-de-prueba" class="row"
 style="align-items:flex-end">{_csrf(ctx)}<div><label for="wa-numero">{
            t("Número para el mensaje de prueba", "Number for the test message")
        }</label><input id="wa-numero" name="numero" required placeholder="+54 9 11 5555-0000"
 style="max-width:240px"></div><button type="submit">{
            t("Enviar mensaje de prueba", "Send test message")
        }</button></form>"""
    phone = esc(found.phone_number_id) if found else ""
    waba = esc(found.waba_id or "") if found else ""
    return f"""<div class="card"><h3>WhatsApp {status}</h3><p class="muted small">{
        t(
            "Conectá el número de WhatsApp Business de tu empresa con tu propia app de Meta. Los "
            "datos se guardan cifrados y el token y el App Secret no se vuelven a mostrar.",
            "Connect your company's WhatsApp Business number with your own Meta app. The data is "
            "stored encrypted, and the token and the App Secret are never shown again.",
        )
    }</p>
<form method="post" action="/panel/configuracion/whatsapp">{_csrf(ctx)}
<label for="wa-phone">Phone Number ID</label><input id="wa-phone" name="phone_number_id"
 required value="{phone}" style="max-width:320px">
<label for="wa-waba">WhatsApp Business Account ID</label><input id="wa-waba" name="waba_id"
 required value="{waba}" style="max-width:320px">
<label for="wa-token">{
        t("Token permanente (de un usuario del sistema)", "Permanent token (from a system user)")
    }</label><input id="wa-token" name="token" type="password" autocomplete="off"
 placeholder="{esc(saved) if found and found.has_token else ""}" style="max-width:420px">
<label for="wa-secret">App Secret</label><input id="wa-secret" name="app_secret"
 type="password" autocomplete="off"
 placeholder="{esc(saved) if found and found.has_app_secret else ""}" style="max-width:420px">
<button type="submit">{t("Guardar", "Save")}</button></form>
{webhook}{actions}
<details style="margin:10px 0"><summary>{
        t(
            "Guía: crear la app en Meta y conectar el número",
            "Guide: create the Meta app and connect the number",
        )
    }</summary><div class="small">{guides.whatsapp_guide()}</div></details></div>"""


def _public_url(request: Request) -> str:
    """La dirección pública del servicio. Detrás de un proxy (Render), el protocolo real llega
    en X-Forwarded-Proto."""
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{request.headers.get('host') or request.url.netloc}"


# Vista previa: cada cambio del formulario se ve en el widget antes de guardar.
@router.post("/configuracion/widget")
async def save_widget(request: Request, data: Files, deps: Deps) -> Response:
    if data.too_big:
        return _back("deploy", error="El logo es demasiado grande.")
    ctx = enter(request, deps, data.fields)
    if isinstance(ctx, Response):
        return ctx
    fields = data.fields
    back = "agent/voice" if fields.get("volver") == "agent/voice" else "deploy"
    try:
        with deps.connection() as conn:
            widget.save(
                conn,
                ctx.tenant_id,
                name=fields.get("nombre", ""),
                accent=fields.get("color", ""),
                domains=fields["dominios"]
                if "dominios" in fields
                else "\n".join(widget.load(conn, ctx.tenant_id).domains),
                suggestions=fields.get("sugerencias", ""),
                logo=data.files[0].data if data.files else None,
                remove_logo=fields.get("quitar_logo") == "si",
                assistant=fields.get("asistente", ""),
                greeting=fields.get("saludo", ""),
            )
    except widget.WidgetError as error:
        return _back(back, error=str(error))
    return _back(back, ok="Widget guardado.")


@router.post("/configuracion/idioma")
def save_language(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    choice = form.get("idioma", "")
    if choice not in i18n.LANGUAGES:
        return _back("agent/language", error="Elegí un idioma de la lista.")
    with deps.connection() as conn:
        i18n.set_language(conn, ctx.tenant_id, choice)
    return _back("agent/language", ok=f"Idioma guardado: {i18n.NAMES[choice]}.")


@router.post("/configuracion/voz")
def save_voice(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            brand_voice.save(
                conn,
                ctx.tenant_id,
                tono=form.get("tono", ""),
                trato=form.get("trato", ""),
                usar=form.get("usar", ""),
                evitar=form.get("evitar", ""),
                emojis=form.get("emojis") == "si",
                firma=form.get("firma", ""),
            )
    except brand_voice.VoiceError as error:
        return _back("agent/voice", error=str(error))
    return _back("agent/voice", ok="Voz de marca guardada.")


@router.post("/configuracion/supervisor")
def save_answer_supervisor(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    on = form.get("activo") == "si"
    with deps.connection() as conn:
        answer_supervisor.set_enabled(conn, ctx.tenant_id, on)
    state = "activado" if on else "desactivado"
    return _back("agent", ok=f"Supervisor de respuestas {state}.")


@router.post("/configuracion/supervisado")
def save_supervised(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    on = form.get("activo") == "si"
    with deps.connection() as conn:
        supervision.set_enabled(conn, ctx.tenant_id, on)
    return _back("agent", ok=f"Modo supervisado {'activado' if on else 'desactivado'}.")


def _behind_html(ctx: Ctx, slug: str | None, on: bool) -> str:
    """Vista "detrás de escena" de la demo (decisión 041): apagada por defecto."""
    if not slug:
        return '<div class="card"><p class="muted">Esta empresa no tiene un chat web.</p></div>'
    link = (
        f'<p>Abrila en <a href="/demo/{esc(slug)}" target="_blank" rel="noopener">'
        f"/demo/{esc(slug)}</a>.</p>"
        if on
        else ""
    )
    state = "Activada" if on else "Desactivada"
    return f"""<div class="card"><div class="row"><div class="grow">
<span class="badge {"resolved" if on else ""}">{state}</span>
<p class="muted small">Una página para mostrar la demo: el sitio con el chat a la izquierda y,
a la derecha, cada paso del caso en vivo (intención, verificación, acciones, reglas del
guardián, fuentes, aprobación, latencia y costo). Es de solo lectura y no muestra mensajes ni
datos personales de los clientes.</p>{link}</div>
<form method="post" action="/panel/configuracion/detras-de-escena">{_csrf(ctx)}
<button name="activo" value="{"no" if on else "si"}" class="{"" if on else "primary"}">
{"Desactivar" if on else "Activar"}</button></form></div></div>"""


@router.post("/configuracion/detras-de-escena")
def save_behind(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    on = form.get("activo") == "si"
    with deps.connection() as conn:
        behind.set_enabled(conn, ctx.tenant_id, on)
    state = "activada" if on else "desactivada"
    return _back("operator/behind", ok=f"Vista detrás de escena {state}.")


@router.post("/configuracion/reglas")
def save_rules(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    days = ",".join(str(n) for n in range(1, 8) if form.get(f"dia_{n}") == "si")
    try:
        hours = (
            rules.parse_hours(days, form.get("desde", ""), form.get("hasta", "")) if days else None
        )
        handoff = rules.parse_handoff_email(form.get("derivar_a", ""))
    except rules.RulesError as error:
        return _back("agent", error=str(error))
    with deps.connection() as conn:
        rules.save(conn, ctx.tenant_id, hours, handoff)
    return _back("agent", ok="Reglas de atención guardadas.")


@router.post("/configuracion/ventas")
def add_upsell(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    kinds = [key.removeprefix("tipo_") for key, value in form.items() if key.startswith("tipo_")]
    try:
        discount = upsell.parse_discount(form.get("descuento", "0"))
        with deps.connection() as conn:
            upsell.add(conn, ctx.tenant_id, form.get("producto", ""), kinds, discount)
    except upsell.UpsellError as error:
        return _back("agent/sales", error=str(error))
    return _back("agent/sales", ok="Producto para ofrecer agregado.")


@router.post("/configuracion/ventas/{rule_id}/borrar")
def remove_upsell(rule_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        upsell.remove(conn, ctx.tenant_id, rule_id)
    return _back("agent/sales", ok="Producto quitado.")


# Una línea de ayuda por pantalla, en lenguaje simple (decisión 085).
LEADS = {
    "Rules": (
        "When your agent answers, when it hands off to your team, and how much it can do on its"
        " own."
    ),
    "Actions": (
        "What your agent can do in your store, like looking up an order or starting a refund. "
        "Nothing runs until you approve it."
    ),
    "Topics": (
        "The kinds of questions your customers ask, learned from your history. Turn on the ones"
        " your agent should handle."
    ),
    "Brand & voice": ("How your agent sounds: its tone, the words it uses and how it signs off."),
    "Sales": ("Products your agent can suggest after it solves the customer's question."),
    "Language": (
        "The language your agent uses with your customers. Your own dashboard language is in "
        "the menu."
    ),
    "Deploy": ("Connect your store, put the agent on your website and WhatsApp, and go live."),
    "Usage & model": (
        "What your agent costs this month, your spending cap and the AI model it uses."
    ),
    "Your data": ("Your data is yours: take it with you or delete it."),
    "Behind the scenes": ("The public demo page that shows each step of a case next to the chat."),
}


# -- secciones del panel (decisión 085): lo que antes era una sola página de Configuración --------
def head(title: str, lead: str = "", actions: str = "") -> str:
    """Encabezado de una pantalla: título, una línea de ayuda y acciones a la derecha."""
    help_line = f'<p class="lead">{esc(lead)}</p>' if lead else ""
    return f'<div class="pagehead"><div><h1>{esc(title)}</h1>{help_line}</div>{actions}</div>'


def _section(request: Request, ctx: Ctx, active: str, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(page(title, _notice(request) + body, active=active, session=ctx.session))


@router.get("/agent", response_class=HTMLResponse)
def agent_rules_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        attention = forms.rules_html(ctx.csrf, rules.load(conn, ctx.tenant_id))
        checks = forms.review_html(
            ctx.csrf,
            supervision.enabled(conn, ctx.tenant_id),
            answer_supervisor.enabled(conn, ctx.tenant_id),
            answer_supervisor.stats(conn, ctx.tenant_id),
        )
    body = f"""{forms.FORM_CSS}{head("Rules", LEADS["Rules"])}
<h2>Business hours and hand-offs</h2>{attention}
<h2>Safety checks</h2>{checks}"""
    return _section(request, ctx, "agent", "Rules", body)


@router.get("/agent/actions", response_class=HTMLResponse)
def agent_actions_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        tools = review.tools(conn, ctx.tenant_id)
    body = f"""{forms.FORM_CSS}{head("Actions", LEADS["Actions"])}
{forms.actions_html(ctx.csrf, tools)}"""
    return _section(request, ctx, "agent/actions", "Actions", body)


@router.get("/agent/topics", response_class=HTMLResponse)
def agent_topics_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        types = review.case_types(conn, ctx.tenant_id)
        tools = review.tools(conn, ctx.tenant_id)
    approved = {t.name for t in tools if t.approved}
    body = f"""{forms.FORM_CSS}{head("Topics", LEADS["Topics"])}
{forms.topics_html(ctx.csrf, types, approved)}"""
    return _section(request, ctx, "agent/topics", "Topics", body)


@router.get("/agent/voice", response_class=HTMLResponse)
def agent_voice_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        language = i18n.tenant_language(conn, ctx.tenant_id)
        voice_found = brand_voice.load(conn, ctx.tenant_id)
        slug = chat_slug(conn, ctx.tenant_id)
        brand = widget.load(conn, ctx.tenant_id)
    company = ctx.session.company
    chat = forms.brand_html(ctx.csrf, brand) if slug else ""
    preview = forms.preview_html(slug, language, company) if slug else ""
    voice = forms.voice_html(ctx.csrf, voice_found, language, company)
    body = f"""{forms.FORM_CSS}{head("Brand & voice", LEADS["Brand & voice"])}
<div class="split"><div><h2 style="margin-top:0">How your chat looks</h2>{chat}
<h2>How your agent writes</h2>{voice}</div><div>{preview}</div></div>"""
    return _section(request, ctx, "agent/voice", "Brand & voice", body)


@router.get("/agent/sales", response_class=HTMLResponse)
def agent_sales_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        offers = forms.sales_html(
            ctx.csrf, upsell.load(conn, ctx.tenant_id), upsell.eligible_types(conn, ctx.tenant_id)
        )
    body = f"""{forms.FORM_CSS}{head("Sales", LEADS["Sales"])}
{offers}"""
    return _section(request, ctx, "agent/sales", "Sales", body)


@router.get("/agent/language", response_class=HTMLResponse)
def agent_language_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        language = i18n.tenant_language(conn, ctx.tenant_id)
    body = f"""{forms.FORM_CSS}{head("Language", LEADS["Language"])}
{forms.language_html(ctx.csrf, language, i18n.NAMES)}"""
    return _section(request, ctx, "agent/language", "Language", body)


def _owner(ctx: Ctx) -> bool:
    return not ctx.session.admin and ctx.session.role != "agent"


@router.get("/settings/data", response_class=HTMLResponse)
def settings_data_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    company = esc(ctx.session.company)
    if ctx.session.admin:
        actions = (
            '<div class="card"><p>The demo company is managed from '
            '<a href="/panel/operator/companies">Companies</a>.</p></div>'
        )
    else:
        actions = f"""<div class="card"><h2>Export your data</h2>
<p class="muted">Download everything {company} has here (conversations, customers, knowledge,
rules and settings) as a .zip file. Passwords and keys are never included.</p>
<a class="btn" href="/panel/settings/data/export">Download .zip</a></div>
<div class="card alert"><h2>Delete your company</h2>
<p>This permanently deletes {company} and all its data: conversations, customers, knowledge,
users and connections. <b>It can't be undone.</b></p>
<form method="post" action="/panel/settings/data/delete"
 data-confirm="Delete your company and all its data? It can't be undone."
 data-confirm-ok="Delete everything">{_csrf(ctx)}
<label for="nombre">Type <b>{company}</b> to confirm</label>
<input id="nombre" name="nombre" required autocomplete="off">
<p><button class="danger solid" type="submit">Delete company and all data</button></p>
</form></div>"""
    body = f"""{head("Your data", LEADS["Your data"])}{actions}"""
    return _section(request, ctx, "settings/data", "Your data", body)


@router.get("/settings/data/export")
def export_own_company(request: Request, deps: Deps) -> Response:
    """Los datos de la propia empresa en un .zip, sin secretos (decisiones 055 y 085)."""
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not _owner(ctx):
        return _forbidden("Esta sección es solo para los admins de la empresa.")
    with deps.connection() as conn:
        data = export.export_company(conn, ctx.tenant_id, operator=ctx.session.email)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="empresa-{ctx.tenant_id}.zip"'},
    )


@router.post("/settings/data/delete")
def delete_own_company(request: Request, form: Form, deps: Deps) -> Response:
    """La empresa borra todos sus datos: hay que escribir su nombre (decisión 085)."""
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if not _owner(ctx):
        return _forbidden("Esta sección es solo para los admins de la empresa.")
    try:
        with deps.connection() as conn:
            removal.delete_company(
                conn, ctx.tenant_id, operator=ctx.session.email, confirm_name=form.get("nombre", "")
            )
    except removal.DeleteError as error:
        return _back("settings/data", error=str(error))
    response = RedirectResponse("/panel/login", status_code=303)
    response.delete_cookie(auth.COOKIE, path="/panel")
    return response


@router.get("/operator/behind", response_class=HTMLResponse)
def operator_behind_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.can_reset:  # el operador o una empresa de demo nuestra
        return _forbidden("Solo el operador de la plataforma puede ver esta sección.")
    with deps.connection() as conn:
        slug = chat_slug(conn, ctx.tenant_id)
        behind_on = behind.enabled(conn, ctx.tenant_id)
    body = f"""{head("Behind the scenes", LEADS["Behind the scenes"])}
{_behind_html(ctx, slug, behind_on)}"""
    return _section(request, ctx, "operator/behind", "Behind the scenes", body)


@router.post("/configuracion/tipos/{type_id}")
def update_case_type(type_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    active = {"si": True, "no": False}.get(form.get("activo", ""))
    instructions = form.get("instrucciones")
    try:
        with deps.connection() as conn:
            found = review.set_case_type(
                conn,
                ctx.tenant_id,
                type_id,
                active=active,
                instructions=instructions.strip() + "\n" if instructions is not None else None,
            )
    except review.ReviewError as error:
        return _back("agent/topics", error=str(error))
    if not found:
        return _not_found("Ese tema", ctx.session)
    return _back("agent/topics", ok="Tema actualizado.")


@router.post("/configuracion/herramientas/{tool_id}")
def update_tool(tool_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    approved = {"si": True, "no": False}.get(form.get("aprobada", ""))
    limits = form.get("limites") == "si"
    # Reglas por acción (decisión 051): casillas sin marcar no llegan en el formulario.
    rules_sent = form.get("reglas") == "si"
    try:
        with deps.connection() as conn:
            writes = next(
                (
                    t.manifest["upstream"]["method"] != "GET"
                    for t in review.tools(conn, ctx.tenant_id)
                    if t.id == tool_id
                ),
                False,
            )
            found = review.set_tool(
                conn,
                ctx.tenant_id,
                tool_id,
                approved=approved,
                max_amount=form.get("monto_maximo", "") if limits else review.UNSET,
                approval_over=form.get("aprobacion_sobre", "") if limits else review.UNSET,
                requires_verified=form.get("identidad") == "si" if rules_sent else None,
                always_approval=form.get("siempre") == "si" if rules_sent and writes else None,
            )
    except review.ReviewError as error:
        return _back("agent/actions", error=str(error))
    if not found:
        return _not_found("Esa acción", ctx.session)
    return _back("agent/actions", ok="Acción actualizada.")


@router.post("/configuracion/sistema/openapi")
async def save_openapi(request: Request, data: Files, deps: Deps) -> Response:
    if data.too_big:
        return _back("deploy", error="El archivo es demasiado grande.")
    ctx = enter(request, deps, data.fields)
    if isinstance(ctx, Response):
        return ctx
    text = (
        data.files[0].data.decode("utf-8-sig", errors="replace")
        if data.files
        else data.fields.get("texto", "")
    )
    if not text.strip():
        return _back("deploy", error="Subí o pegá el OpenAPI.")
    try:
        with deps.connection() as conn:
            found = system.save_openapi(conn, ctx.tenant_id, text)
    except system.ConnectionError_ as error:
        return _back("deploy", error=str(error))
    return _back("deploy", ok=f"OpenAPI guardado: {found.operations} operaciones.")


@router.post("/configuracion/sistema/acceso")
def save_access(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            system.save_access(
                conn,
                ctx.tenant_id,
                base_url=form.get("url", ""),
                auth_kind=form.get("auth", "none"),
                auth_header=form.get("encabezado"),
                secret=form.get("credencial") or None,
            )
    except system.ConnectionError_ as error:
        return _back("deploy", error=str(error))
    return _back("deploy", ok="Acceso guardado. Ahora probá la conexión.")


@router.post("/configuracion/sistema/probar")
def test_system(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            return _tested(conn, ctx, deps)
    except system.ConnectionError_ as error:
        return _back("deploy", error=str(error))


def _tested(conn: Any, ctx: Ctx, deps: PanelDeps, connected: bool = False) -> Response:
    """Prueba la conexión y vuelve a Deploy. Si pasó, los dominios de la tienda se suman a los
    del chat; si no, la pantalla dice qué falta y qué hacer (decisión 086)."""
    result = system.test_connection(conn, ctx.tenant_id, client_factory=deps.client_factory)
    if not result["ok"]:
        # El diagnóstico completo se ve en el paso 1; acá, el aviso.
        found = deploy.explain(result, deploy.platform_of(system.load(conn, ctx.tenant_id)))
        return _back("deploy", error=f"{found.what}\n{found.todo}" if found else "")
    added = deploy.add_store_domains(conn, ctx.tenant_id, result)
    done = "Store connected." if connected else "Connection test passed."
    if added:
        done += f" We added {', '.join(added)} to your chat's allowed domains."
    return _back("deploy", ok=f"{done} You can now use your real store.")


@router.post("/configuracion/sistema/plataforma")
def connect_platform(request: Request, form: Form, deps: Deps) -> Response:
    """Shopify o Tiendanube, sin OpenAPI (decisiones 057 y 067). Shopify: con el Client ID y
    el Client Secret de una app del Dev Dashboard (`metodo=app`) o con el token de una app
    legacy (`metodo=token`)."""
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    method = form.get("metodo") or ("app" if form.get("client_id") else "token")
    try:
        with deps.connection() as conn:
            platforms.connect(
                conn,
                ctx.tenant_id,
                form.get("plataforma", ""),
                form.get("tienda", ""),
                form.get("token", ""),
                method=method if method in ("app", "token") else "token",
                client_id=form.get("client_id", ""),
                client_secret=form.get("client_secret", ""),
            )
    except shopify_auth.TokenError as error:
        found = deploy.explain({"ok": False, "codigo": error.code}, "shopify")
        return _back("deploy", error=f"{found.what}\n{found.todo}" if found else str(error))
    except httpx.HTTPError:
        found = deploy.explain({"ok": False, "codigo": "shop_unreachable"}, "shopify")
        return _back("deploy", error=f"{found.what}\n{found.todo}" if found else "")
    except ValueError as error:
        return _back("deploy", error=str(error))
    # Conectar prueba la conexión en el momento (decisión 086).
    with deps.connection() as conn:
        return _tested(conn, ctx, deps, connected=True)


OAUTH_COOKIE = "shopify_oauth"
OAUTH_PATH = "/panel/configuracion/sistema/shopify"


@router.post("/configuracion/sistema/shopify/instalar")
def install_shopify(request: Request, form: Form, deps: Deps) -> Response:
    """Instalación por OAuth (decisión 067): lleva a Shopify para que la tienda apruebe la app
    de la plataforma. El state firmado va en la URL y en una cookie de este navegador."""
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    app_keys = shopify_auth.app_credentials()
    if app_keys is None:
        return _back("deploy", error="La instalación con Shopify no está configurada.")
    try:
        shop = shopify.clean_shop(form.get("tienda", ""))
    except ValueError as error:
        return _back("deploy", error=str(error))
    state = shopify_auth.make_state(ctx.tenant_id, shop)
    callback = f"{_public_url(request)}{OAUTH_PATH}/callback"
    response = RedirectResponse(
        shopify_auth.authorize_url(shop, app_keys[0], callback, state), status_code=303
    )
    response.set_cookie(
        OAUTH_COOKIE,
        state,
        max_age=shopify_auth.STATE_SECONDS,
        path=OAUTH_PATH,
        httponly=True,
        secure=request.url.scheme == "https" or _public_url(request).startswith("https"),
        samesite="lax",
    )
    return response


@router.get("/configuracion/sistema/shopify/callback")
def shopify_callback(request: Request, deps: Deps) -> Response:
    """Vuelta de Shopify: HMAC con el secreto de la app, state firmado, de esta empresa, de
    esta tienda y el mismo de la cookie. Recién ahí se cambia el código por el token."""
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    params = dict(request.query_params)
    app_keys = shopify_auth.app_credentials()

    def fail(message: str, *, used: bool = False) -> Response:
        # Un callback falso no borra la cookie: no puede cortar una instalación en curso.
        response = _back("deploy", error=message)
        if used:
            response.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH)
        return response

    if app_keys is None:
        return fail("La instalación con Shopify no está configurada.")
    try:
        shop = shopify.clean_shop(params.get("shop", ""))
    except ValueError:
        return fail("Shopify devolvió una tienda inválida.")
    if not shopify_auth.valid_hmac(params, app_keys[1]):
        return fail("La respuesta de Shopify no tiene una firma válida. Probá instalar de nuevo.")
    state = params.get("state", "")
    claims = shopify_auth.read_state(state, shop)
    cookie = request.cookies.get(OAUTH_COOKIE, "")
    if (
        claims is None
        or claims.get("t") != str(ctx.tenant_id)
        or not cookie
        or not hmac.compare_digest(cookie, state)
    ):
        return fail("La instalación venció o no empezó acá. Probá instalar de nuevo.")
    try:
        creds = shopify_auth.exchange_code(
            shop, params.get("code", ""), *app_keys, transport=platforms.UPSTREAM
        )
        with deps.connection() as conn:
            platforms.connect(conn, ctx.tenant_id, "shopify", shop, credentials=creds)
    except (ValueError, httpx.HTTPError) as error:
        message = str(error) if isinstance(error, ValueError) else "Shopify no respondió."
        return fail(message, used=True)
    response = _back("deploy", ok="Tienda conectada. Probá la conexión y aprobá sus acciones.")
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH)
    return response


@router.post("/configuracion/sistema/herramientas")
def probe_system_tools(request: Request, form: Form, deps: Deps) -> Response:
    """Prueba cada acción de solo lectura con un pedido de ejemplo (decisión 057)."""
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            results = system.probe_tools(
                conn, ctx.tenant_id, form.get("ejemplo", ""), client_factory=deps.client_factory
            )
    except system.ConnectionError_ as error:
        return _back("deploy", error=str(error))
    summary = " · ".join(f"{tool_label(r['acción'])}: {r['detalle']}" for r in results)
    failed = any(r["ok"] is False for r in results)
    return _back("deploy", **({"warning": summary} if failed else {"ok": summary}))


@router.post("/configuracion/sistema/activar")
def activate_system(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            system.activate(conn, ctx.tenant_id)
    except system.ConnectionError_ as error:
        return _back("deploy", error=str(error))
    return _back("deploy", ok="Listo: el agente ya usa tu sistema real.")


@router.post("/configuracion/sistema/desactivar")
def deactivate_system(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        system.deactivate(conn, ctx.tenant_id)
    return _back("deploy", ok="El agente volvió al modo simulado.")


# -- Canales → WhatsApp (self-service, decisión 068) ------------------------------------------
def _whatsapp_action(
    request: Request, form: dict[str, str], deps: PanelDeps, action: Any
) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            message = action(conn, ctx.tenant_id)
    except whatsapp.SetupError as error:
        return _back("deploy", error=str(error))
    except httpx.HTTPError as error:
        return _back("deploy", error=f"Meta no respondió ({type(error).__name__}).")
    if isinstance(message, tuple):
        return _back("deploy", error=message[0])
    return _back("deploy", ok=message)


@router.post("/configuracion/whatsapp")
def save_whatsapp(request: Request, form: Form, deps: Deps) -> Response:
    def run(conn: Any, tenant_id: uuid.UUID) -> str:
        whatsapp.save(
            conn,
            tenant_id,
            phone_number_id=form.get("phone_number_id", ""),
            waba_id=form.get("waba_id", ""),
            token=form.get("token", ""),
            app_secret=form.get("app_secret", ""),
        )
        return "WhatsApp guardado. Pegá el webhook en Meta y probá la conexión."

    return _whatsapp_action(request, form, deps, run)


@router.post("/configuracion/whatsapp/probar")
def test_whatsapp(request: Request, form: Form, deps: Deps) -> Response:
    def run(conn: Any, tenant_id: uuid.UUID) -> str | tuple[str]:
        result = whatsapp.test(conn, tenant_id)
        if result["ok"]:
            return f"La prueba pasó. {result['detalle']} Ya podés activar."
        return (f"La prueba falló: {result['detalle']}",)

    return _whatsapp_action(request, form, deps, run)


@router.post("/configuracion/whatsapp/mensaje-de-prueba")
def send_whatsapp_test(request: Request, form: Form, deps: Deps) -> Response:
    def run(conn: Any, tenant_id: uuid.UUID) -> str:
        whatsapp.send_test(conn, tenant_id, form.get("numero", ""))
        return "Mensaje de prueba enviado. Revisá ese WhatsApp."

    return _whatsapp_action(request, form, deps, run)


@router.post("/configuracion/whatsapp/activar")
def activate_whatsapp(request: Request, form: Form, deps: Deps) -> Response:
    def run(conn: Any, tenant_id: uuid.UUID) -> str:
        whatsapp.activate(conn, tenant_id)
        return "WhatsApp activo: el agente ya responde en tu número."

    return _whatsapp_action(request, form, deps, run)


@router.post("/configuracion/whatsapp/desactivar")
def deactivate_whatsapp(request: Request, form: Form, deps: Deps) -> Response:
    def run(conn: Any, tenant_id: uuid.UUID) -> str:
        whatsapp.deactivate(conn, tenant_id)
        return "WhatsApp desactivado: el agente ya no responde en tu número."

    return _whatsapp_action(request, form, deps, run)


# Settings → Usage & model y Usage (decisión 102), con el prefijo /panel de este router.
from app.panel.usage_routes import router as _usage_router  # noqa: E402

router.include_router(_usage_router)
