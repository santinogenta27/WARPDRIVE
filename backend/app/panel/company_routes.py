"""Panel, pantallas del hito 6 (decisión 026): alta de empresas, onboarding sin comandos,
revisión de tipos de caso y herramientas, y conexión con el sistema de la empresa.

Todas las rutas entran por `enter` (app/panel/routes.py): la empresa sale de la sesión y cada
consulta corre dentro de `tenant_scope`, así que un id de otra empresa responde 404 igual que
uno que no existe. Los POST exigen el token anti-CSRF.
"""

import hmac
import re
import uuid
from decimal import Decimal, InvalidOperation
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
from app.companies import export, onboarding, review, rules
from app.companies.create import CompanyError, chat_slug, create_company
from app.connectors import platforms, shopify, shopify_auth
from app.demo import behind
from app.llm import spending, tenant_llm
from app.onboarding import ingest as ingestion
from app.onboarding.ingest import Upload
from app.panel import auth, english, guides
from app.panel.guides import guides_html
from app.panel.html import esc, local_time, page
from app.panel.labels import money, tool_label, type_label
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
RISKS = {"low": "Bajo", "medium": "Medio", "high": "Alto"}
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
        return f'<div class="card alert">{esc(error[:400])}</div>'
    # `aviso`: algo que no se pudo hacer aunque el resto sí (un PDF escaneado en una carga).
    warning = request.query_params.get("aviso")
    shown = f'<div class="card okmsg">{esc(ok[:400])}</div>' if ok else ""
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
    "tools": "herramientas",
    "case_types": "tipos de caso",
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
<form method="post" action="/panel/empresas">{_csrf(ctx)}
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
@router.get("/knowledge", response_class=HTMLResponse)
def onboarding_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = onboarding.sources(conn, ctx.tenant_id)
        names = sorted(onboarding.business_names(conn, ctx.tenant_id) - {ctx.session.company})
        run = onboarding.state(conn, ctx.tenant_id)
        waiting = ingestion.pending_documents(conn, ctx.tenant_id)
        indexed = ingestion.indexed_documents(conn, ctx.tenant_id)
    source_rows = "".join(
        f"<tr><td>{esc(name)}</td><td>{esc(KINDS.get(kind, kind))}</td>"
        f'<td class="num">{count or "—"}</td><td>{local_time(created)}</td></tr>'
        for name, kind, count, created in found
    )
    sources_table = (
        f"<table><tr><th>Archivo</th><th>Tipo</th><th class=num>Conversaciones</th>"
        f"<th>Subido</th></tr>{source_rows}</table>"
        if source_rows
        else '<p class="muted">Todavía no subiste nada.</p>'
    )
    steps = "".join(
        f'<li><span class="badge {esc(status)}">{STAGE_STATUS.get(status, status)}</span>'
        f'<span class="grow"><b>{position}. {esc(label)}</b>'
        f'<div class="muted small">{esc(detail)}</div></span></li>'
        for position, (_, label, status, detail) in enumerate(onboarding.progress(run), start=1)
    )
    running = run is not None and run.queued
    if running:
        action = (
            '<p class="muted">El análisis está corriendo. Esta página se actualiza sola; podés '
            "cerrarla y volver más tarde.</p>"
        )
    else:
        label = (
            "Analizar mi historial"
            if run is None
            else "Reintentar desde donde quedó"
            if run.status != "done"
            else "Analizar de nuevo (con las fuentes nuevas)"
        )
        action = (
            f'<form method="post" action="/panel/onboarding/iniciar">{_csrf(ctx)}'
            f'<button class="primary" type="submit">{label}</button></form>'
        )
    done = (
        '<div class="card okmsg"><b>El análisis terminó.</b> Mirá el '
        '<a href="/panel/test">reporte de preparación</a> y después revisá y aprobá los tipos '
        'de caso y las herramientas en <a href="/panel/agent/topics">Configuración</a>.</div>'
        if run is not None and run.status == "done" and not running
        else ""
    )
    body = f"""<h1>Onboarding</h1><p class="lead">Subí el historial de atención y los documentos
de {esc(ctx.session.company)}. El asistente aprende de ahí: detecta los tipos de caso, arma sus
pruebas y te dice qué parte de la atención resolvería solo.</p>{_notice(request)}{done}
{_pending_html(ctx, waiting)}
<div class="card"><h2 style="margin-top:0">1. Subí tus fuentes</h2>{sources_table}
<form method="post" action="/panel/onboarding/fuentes" enctype="multipart/form-data"
 style="margin-top:12px">{_csrf(ctx)}
<label for="archivos">Historial de chats y documentos</label>
<input id="archivos" name="archivos" type="file" multiple required
 accept=".txt,.zip,.csv,.json,.md,.docx,.pdf">
<p class="muted small">Chats de WhatsApp exportados (.txt, o un .zip con varios), tickets de
helpdesk (.csv o .json) y documentos de políticas (.md, .txt, .docx, .pdf con texto o una tabla
.csv, como un catálogo). Hasta
50 MB. En los chats y tickets, los datos personales se reemplazan por marcadores antes de
guardar. En los documentos se reemplazan los números de tarjeta y los DNI; los emails y
teléfonos de tu empresa quedan como están. El archivo original no se conserva.</p>
<button class="primary" type="submit">Subir</button></form>
<form method="post" action="/panel/onboarding/nombres" style="margin-top:16px">{_csrf(ctx)}
<label for="nombres">Con qué nombre figura la empresa en los chats de WhatsApp</label>
<input id="nombres" name="nombres" value="{esc(", ".join(names))}"
 placeholder="Por ejemplo: Soporte Tienda, Ana de Tienda">
<p class="muted small">Sirve para saber qué mensajes son del equipo y cuáles del cliente.
Separá los nombres con comas y cargalos antes de subir los chats.</p>
<button type="submit">Guardar nombres</button></form>
<form method="post" action="/panel/onboarding/sitio" style="margin-top:16px">{_csrf(ctx)}
<label for="sitio">Sitio web de la empresa</label>
<input id="sitio" name="sitio" type="url" required placeholder="https://www.tutienda.com">
<p class="muted small">Se leen hasta 30 páginas, sin salir de tu dominio. Volver a cargarlo
actualiza las páginas.</p><button type="submit">Leer el sitio</button></form></div>
{_indexed_html(ctx, indexed)}
<div class="card"><h2 style="margin-top:0">2. Análisis</h2>
<ol class="steps">{steps}</ol><div style="margin-top:12px">{action}</div></div>"""
    return HTMLResponse(
        page(
            "Onboarding",
            body,
            active="knowledge",
            session=ctx.session,
            refresh=8 if running else None,
        )
    )


def _indexed_html(ctx: Ctx, indexed: list[ingestion.IndexedDocument]) -> str:
    """Lo que el asistente puede citar (decisión 050). Subir un archivo con el mismo nombre lo
    reemplaza; borrarlo lo saca de las respuestas en el momento."""
    if not indexed:
        return ""
    rows = "".join(
        f"<tr><td>{esc(doc.title)}</td><td class=mono>{esc(doc.source)}</td>"
        f'<td class="num">{doc.chunks}</td><td>{local_time(doc.created_at)}</td>'
        f'<td><form method="post" action="/panel/onboarding/indexados/{doc.id}/borrar">'
        f'{_csrf(ctx)}<button type="submit">Borrar</button></form></td></tr>'
        for doc in indexed
    )
    return f"""<div class="card"><h2 style="margin-top:0">Documentos indexados</h2>
<p class="muted small">Lo que el asistente puede citar. Para actualizar uno, subí el archivo de
nuevo con el mismo nombre: reemplaza al anterior.</p>
<table><tr><th>Título</th><th>Archivo</th><th class=num>Fragmentos</th><th>Indexado</th><th></th>
</tr>{rows}</table></div>"""


@router.post("/onboarding/indexados/{document_id}/borrar")
def delete_indexed(document_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = ingestion.delete_document(conn, ctx.tenant_id, document_id)
    if not found:
        return _not_found("Ese documento", ctx.session)
    return _back("knowledge", ok="Documento borrado: el asistente ya no lo usa.")


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
asistente responde con lo que dicen tus documentos: si uno trae emails o teléfonos de clientes,
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
<form method="post" action="/panel/empresas/{tenant_id}/borrar">{_csrf(ctx)}
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


# -- configuración: tipos de caso, herramientas, sistema y chat ------------------------------
MODES = {
    "demo": ("resolved", "Sistema de demo (API simulada de Acme)"),
    "real": ("resolved", "Conectado al sistema real"),
    "simulado": ("waiting_approval", "Modo simulado: las herramientas no tocan tu sistema"),
    "sin_sistema": ("escalated", "Sin sistema conectado: el asistente solo informa"),
}


def _types_html(ctx: Ctx, types: list[review.CaseType], approved: set[str]) -> str:
    if not types:
        return (
            '<p class="muted">Todavía no hay tipos de caso. Salen del análisis del '
            '<a href="/panel/knowledge">onboarding</a>.</p>'
        )
    cards = []
    for kind in types:
        missing = [t for t in kind.tools if t not in approved]
        warning = (
            f'<div class="missing">Usa herramientas sin aprobar: '
            f"{esc(', '.join(tool_label(t) for t in missing))}. Hasta aprobarlas, no las usa.</div>"
            if missing
            else ""
        )
        tools = ", ".join(tool_label(t) for t in kind.tools) or "solo información"
        cards.append(
            f"""<div class="card"><div class="row"><div class="grow">
<span class="badge {"resolved" if kind.active else ""}">{"Activo" if kind.active else "Inactivo"}
</span> <b>{esc(type_label(kind.name))}</b>
<div class="muted small">{esc(kind.goal)}</div>
<div class="muted small">{kind.volume} conversaciones del historial · Herramientas: {esc(tools)}
</div>{warning}</div>
<form method="post" action="/panel/configuracion/tipos/{kind.id}">{_csrf(ctx)}
<button class="{"" if kind.active else "primary"}" name="activo"
 value="{"no" if kind.active else "si"}">{"Desactivar" if kind.active else "Activar"}</button>
</form></div>
<details class="small" style="margin-top:8px"><summary class="muted">Ver y editar las
instrucciones</summary>
<form method="post" action="/panel/configuracion/tipos/{kind.id}">{_csrf(ctx)}
<textarea name="instrucciones" style="min-height:190px" class="mono">{esc(kind.instructions)}
</textarea><button type="submit">Guardar instrucciones</button></form></details></div>"""
        )
    return "".join(cards)


def _tools_html(ctx: Ctx, tools: list[review.Tool]) -> str:
    if not tools:
        return (
            '<p class="muted">Todavía no hay herramientas. Se generan en el análisis a partir '
            "del OpenAPI de tu sistema (cargalo más abajo y volvé a analizar).</p>"
        )
    cards = []
    for tool in tools:
        m = tool.manifest
        high = m["risk"] == "high"
        writes = m["upstream"]["method"] != "GET"
        limits = ""
        if writes:
            over = (
                '<input value="Siempre (riesgo alto)" disabled>'
                if high
                else f'<input name="aprobacion_sobre" inputmode="decimal" '
                f'value="{esc(_plain(m.get("approval_over")))}" placeholder="Nunca">'
            )
            limits = f"""<form method="post" action="/panel/configuracion/herramientas/{tool.id}"
 class="row" style="margin-top:10px;align-items:flex-end">{_csrf(ctx)}
<div class="grow"><label>Pedir aprobación de una persona sobre $</label>{over}</div>
<div class="grow"><label>Rechazar siempre sobre $</label>
<input name="monto_maximo" inputmode="decimal" value="{esc(_plain(m.get("max_amount")))}"
 placeholder="Sin tope"></div>
<button name="limites" value="si" type="submit">Guardar límites</button></form>"""
        facts = [
            f"Riesgo {RISKS.get(m['risk'], m['risk']).lower()}",
            "pide identidad verificada" if m.get("requires_verified_contact") else "sin identidad",
            "modifica datos" if writes else "solo consulta",
        ]
        if m.get("max_amount") is not None and m.get("amount_from") == "input.percent":
            facts.append(f"tope {m['max_amount']}%")  # un descuento (decisión 039)
        elif m.get("max_amount") is not None:
            facts.append(f"tope {money(m['max_amount'])}")
        checked = " checked" if m.get("requires_verified_contact") else ""
        always = " checked" if high else ""
        rules_form = f"""<form method="post" action="/panel/configuracion/herramientas/{tool.id}"
 class="row small" style="margin-top:8px;gap:16px">{_csrf(ctx)}
<input type="hidden" name="reglas" value="si">
<label style="font-weight:400"><input type="checkbox" name="identidad" value="si"{checked}>
Exige identidad verificada</label>
{
            f'<label style="font-weight:400"><input type="checkbox" name="siempre" value="si"'
            f"{always}> Siempre pide aprobación</label>"
            if writes
            else ""
        }
<button type="submit">Guardar reglas</button></form>"""
        cards.append(
            f"""<div class="card"><div class="row"><div class="grow">
<span class="badge {"resolved" if tool.approved else ""}">
{"Aprobada" if tool.approved else "Sin aprobar"}</span> <b>{esc(tool_label(tool.name))}</b>
<div class="muted small">{esc(m.get("description"))}</div>
<div class="muted small"><span class="mono">{esc(m["upstream"]["method"])}
{esc(m["upstream"]["path"])}</span> · {esc(" · ".join(facts))}</div></div>
<form method="post" action="/panel/configuracion/herramientas/{tool.id}">{_csrf(ctx)}
<button class="{"" if tool.approved else "primary"}" name="aprobada"
 value="{"no" if tool.approved else "si"}">
{"Quitar aprobación" if tool.approved else "Aprobar"}</button></form></div>{rules_form}{limits}
</div>"""
        )
    return "".join(cards)


def _plain(value: Any) -> str:
    if value is None:
        return ""
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


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
            f'<form method="post" action="/panel/configuracion/whatsapp/desactivar">{_csrf(ctx)}'
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


def _platforms_html(ctx: Ctx) -> str:
    """Shopify (tres métodos de conexión y la guía del Dev Dashboard, decisión 067) y
    Tiendanube (token). El texto sale en el idioma del panel."""
    en = english.english()

    def t(es: str, en_text: str) -> str:
        return en_text if en else es

    oauth = ""
    if shopify_auth.app_credentials():
        oauth = f"""<form method="post" action="/panel/configuracion/sistema/shopify/instalar"
 class="row" style="align-items:flex-end">{_csrf(ctx)}<div><label for="oauth-tienda">{
            t("Tienda de un cliente", "A client's store")
        }</label><input id="oauth-tienda"
 name="tienda" required placeholder="mitienda.myshopify.com" style="max-width:360px"></div>
<button type="submit">{t("Instalar con Shopify", "Install with Shopify")}</button></form>
<p class="muted small">{
            t(
                "Abre Shopify para que la tienda apruebe la app de la plataforma con los permisos "
                "mínimos. Volvés acá con la tienda conectada.",
                "Opens Shopify so the store approves the platform's app with the minimum scopes. "
                "You come back here with the store connected.",
            )
        }</p>"""
    return f"""<h3>{t("¿Usás Shopify?", "Do you use Shopify?")}</h3><p class="muted small">{
        t(
            "Conectá tu tienda sin OpenAPI: se crean las herramientas de pedidos, envíos, "
            "reembolsos, cupones y cancelaciones, sin aprobar. Después probá la conexión y "
            "aprobalas más arriba.",
            "Connect your store without an OpenAPI: the order, shipping, refund, coupon and "
            "cancellation tools are created, unapproved. Then test the connection and approve them "
            "above.",
        )
    }</p>
<form method="post" action="/panel/configuracion/sistema/plataforma">{_csrf(ctx)}
<input type="hidden" name="plataforma" value="shopify">
<label for="shop-tienda">{t("Tienda", "Store")}</label><input id="shop-tienda" name="tienda"
 required placeholder="mitienda.myshopify.com" style="max-width:360px">
<p class="small" style="margin:10px 0 4px"><b>{t("Método de conexión", "Connection method")}</b>
</p><label style="font-weight:400"><input type="radio" name="metodo" value="app" checked> {
        t(
            "App del Dev Dashboard: Client ID y Client Secret (recomendado)",
            "Dev Dashboard app: Client ID and Client Secret (recommended)",
        )
    }</label><label style="font-weight:400"><input type="radio" name="metodo" value="token"> {
        t(
            "Token de una app legacy que tu tienda ya tiene (shpat_…)",
            "Token of a legacy app your store already has (shpat_…)",
        )
    }</label>
<label for="client_id">Client ID</label><input id="client_id" name="client_id"
 autocomplete="off" style="max-width:420px">
<label for="client_secret">Client Secret</label><input id="client_secret" name="client_secret"
 type="password" autocomplete="off" style="max-width:420px">
<label for="shop-token">{t("Token de acceso (solo app legacy)", "Access token (legacy app only)")}
</label><input id="shop-token" name="token" type="password" autocomplete="off"
 style="max-width:420px"><button type="submit">{t("Conectar Shopify", "Connect Shopify")}</button>
</form>{oauth}
<details style="margin:10px 0"><summary>{
        t(
            "Guía: crear la app en el Dev Dashboard y copiar sus credenciales",
            "Guide: create the app in the Dev Dashboard and copy its credentials",
        )
    }</summary><div class="small">{guides.shopify_app_guide()}</div></details>
<h3>{t("¿Usás Tiendanube?", "Do you use Tiendanube?")}</h3>
<form method="post" action="/panel/configuracion/sistema/plataforma">{_csrf(ctx)}
<input type="hidden" name="plataforma" value="tiendanube">
<label for="tn-tienda">{t("Número de tu tienda", "Your store number")}</label>
<input id="tn-tienda" name="tienda" required style="max-width:360px">
<label for="tn-token">{t("Token de acceso (de tu app)", "Access token (from your app)")}</label>
<input id="tn-token" name="token" type="password" autocomplete="off" required
 style="max-width:420px"><button type="submit">{t("Conectar Tiendanube", "Connect Tiendanube")}
</button></form>"""


def _system_html(ctx: Ctx, found: system.SystemConnection | None, mode: str) -> str:
    badge, label = MODES[mode]
    if mode == "demo" and found is not None and found.base_url != system.DEMO_BASE_URL:
        # Otra empresa de demo (Northwind, decisión 038): su propia API simulada.
        return (
            f'<div class="card"><span class="badge {badge}">Sistema de demo (API simulada)</span>'
            '<p class="muted small">Esta empresa usa la API simulada de la demo: sus herramientas '
            "llegan a pedidos de prueba, nunca a un sistema real.</p></div>"
        )
    if mode == "demo":
        return (
            f'<div class="card"><span class="badge {badge}">{label}</span><p class="muted small">'
            "Acme usa la API simulada de pedidos de la demo. Las demás empresas conectan acá su "
            "sistema real.</p></div>"
        )
    spec_note = (
        f"OpenAPI cargado: {found.operations} operaciones."
        if found and found.openapi
        else "Todavía no cargaste el OpenAPI."
    )
    test = ""
    if found and found.last_test:
        last = found.last_test
        ok = bool(last.get("ok"))
        test = (
            f'<div class="card {"okmsg" if ok else "alert"}" style="margin-top:10px">'
            f"<b>{'La prueba pasó' if ok else 'La prueba falló'}.</b> {esc(last.get('detalle'))} "
            f'<span class="muted small mono">{esc(last.get("operacion"))} · '
            f"{esc(last.get('status', '—'))} · {esc(last.get('ms'))} ms</span></div>"
        )
    kind = found.auth_kind if found else "none"
    options = "".join(
        f'<option value="{value}"{" selected" if value == kind else ""}>{text}</option>'
        for value, text in (
            ("none", "Sin autenticación"),
            ("bearer", "Token (Authorization: Bearer)"),
            ("api_key", "Clave de API en un encabezado"),
            ("basic", "Usuario y clave (Basic)"),
        )
    )
    saved = "Guardadas. Dejalo vacío para conservarlas." if found and found.has_credentials else ""
    status = found.status if found else "draft"
    activate = ""
    if status == "tested":
        activate = (
            f'<form method="post" action="/panel/configuracion/sistema/activar">{_csrf(ctx)}'
            '<button class="primary" type="submit">Activar: usar mi sistema real</button></form>'
        )
    elif status == "active":
        activate = (
            f'<form method="post" action="/panel/configuracion/sistema/desactivar">{_csrf(ctx)}'
            '<button type="submit">Volver al modo simulado</button></form>'
        )
    probe = (
        f'<form method="post" action="/panel/configuracion/sistema/probar">{_csrf(ctx)}'
        '<button type="submit">Probar conexión</button></form>'
        if found and found.base_url
        else ""
    )
    tools_probe = (
        f'<form method="post" action="/panel/configuracion/sistema/herramientas" class="row"'
        f' style="align-items:flex-end">{_csrf(ctx)}<div><label for="ejemplo">Número de pedido'
        ' de ejemplo</label><input id="ejemplo" name="ejemplo" required placeholder="1001"'
        ' style="max-width:200px"></div><button type="submit">Probar herramientas</button></form>'
        if found and found.base_url
        else ""
    )
    return f"""<div class="card"><span class="badge {badge}">{label}</span>
{_platforms_html(ctx)}
<h3>1. OpenAPI de tu sistema</h3><p class="muted small">{esc(spec_note)} Con el OpenAPI, el
análisis genera las herramientas del asistente. Subí el archivo (.json o .yaml) o pegalo.</p>
<form method="post" action="/panel/configuracion/sistema/openapi" enctype="multipart/form-data">
{_csrf(ctx)}<input name="archivo" type="file" accept=".json,.yaml,.yml">
<textarea name="texto" class="mono" placeholder="…o pegá acá el OpenAPI"></textarea>
<button type="submit">Guardar OpenAPI</button></form>
<h3>2. URL base y credenciales</h3>
<form method="post" action="/panel/configuracion/sistema/acceso">{_csrf(ctx)}
<label for="url">URL base (HTTPS)</label>
<input id="url" name="url" type="url" required placeholder="https://api.tuempresa.com"
 value="{esc(found.base_url if found and found.base_url else "")}">
<label for="auth">Autenticación</label><select id="auth" name="auth">{options}</select>
<label for="encabezado">Nombre del encabezado (solo para clave de API)</label>
<input id="encabezado" name="encabezado" placeholder="X-API-Key"
 value="{esc(found.auth_header if found and found.auth_header else "")}">
<label for="credencial">Credencial (token, clave de API o usuario:clave)</label>
<input id="credencial" name="credencial" type="password" autocomplete="off"
 placeholder="{esc(saved)}">
<p class="muted small">Se guarda cifrada con una clave propia de tu empresa y no se vuelve a
mostrar. Cualquier cambio obliga a probar la conexión de nuevo.</p>
<button type="submit">Guardar acceso</button></form>
<h3>3. Probar y activar</h3><p class="muted small">La prueba hace una consulta de solo lectura
a tu sistema con tus credenciales. Hasta que actives, el asistente usa respuestas simuladas.</p>
<div class="row">{probe}{activate}</div>{test}{tools_probe}</div>"""


def _public_url(request: Request) -> str:
    """La dirección pública del servicio. Detrás de un proxy (Render), el protocolo real llega
    en X-Forwarded-Proto."""
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{request.headers.get('host') or request.url.netloc}"


# Vista previa: cada cambio del formulario se ve en el widget antes de guardar.
WIDGET_PREVIEW_JS = """(function(){var f=document.getElementById("widget-form");if(!f)return;
var logo=null;function v(n){return f.elements[n].value}
function push(){if(!window.EnterpriseChat)return;var c={name:v("nombre").trim(),accent:v("color"),
suggestions:v("sugerencias").split("\\n").map(function(s){return s.trim()}).filter(Boolean)
.slice(0,4),greeting:v("saludo").trim()};
if(logo!==null)c.logo=logo;window.EnterpriseChat.setBrand(c)}
f.elements.saludo.addEventListener("input",push);
f.elements.pick.addEventListener("input",function(){f.elements.color.value=this.value;push()});
f.elements.color.addEventListener("input",function(){if(/^#[0-9a-fA-F]{6}$/.test(this.value))
f.elements.pick.value=this.value;push()});
f.elements.nombre.addEventListener("input",push);f.elements.sugerencias.addEventListener("input",push);
f.elements.logo.addEventListener("change",function(){var file=this.files[0];if(!file)return;
var r=new FileReader();r.onload=function(){logo=r.result;push()};r.readAsDataURL(file)});
var copy=document.getElementById("copy-snippet");if(copy)copy.addEventListener("click",function(){
var text=document.getElementById("snippet").textContent;
(navigator.clipboard?navigator.clipboard.writeText(text):Promise.reject()).then(function(){
copy.textContent=copy.getAttribute("data-done")},function(){var r=document.createRange();
r.selectNodeContents(document.getElementById("snippet"));var s=getSelection();s.removeAllRanges();
s.addRange(r)})})})();"""


def _widget_html(ctx: Ctx, request: Request, slug: str, found: widget.WidgetSettings) -> str:
    """Tarjeta "Widget para tu sitio": el código para copiar, los dominios, la marca y la
    vista previa (el widget real, dentro de la página)."""
    code = widget.snippet(_public_url(request), slug)
    logo_note = (
        '<label style="font-weight:400"><input type="checkbox" name="quitar_logo" value="si"> '
        "Quitar el logo actual</label>"
        if found.has_logo
        else ""
    )
    return f"""<div class="card"><h3 style="margin-top:0">1. Pegá este código en tu sitio</h3>
<p class="muted small">Va antes de <span class="mono">&lt;/body&gt;</span>, en todas las páginas
donde quieras el chat. Aparece una burbuja abajo a la derecha.</p>
<pre id="snippet">{esc(code)}</pre>
<button type="button" id="copy-snippet" data-done="Copiado">Copiar código</button>
{guides_html()}
<form method="post" action="/panel/configuracion/widget" enctype="multipart/form-data"
 id="widget-form">{_csrf(ctx)}
<h3>2. Dominios permitidos</h3>
<label for="dominios">Sitios donde se puede usar el chat (uno por línea)</label>
<textarea id="dominios" name="dominios" class="mono" style="min-height:70px"
 placeholder="www.tutienda.com&#10;*.tutienda.com">{esc(chr(10).join(found.domains))}</textarea>
<p class="muted small">El chat solo funciona en estos sitios. Sin ninguno, el widget no se
muestra en sitios ajenos. Usá <span class="mono">*.tutienda.com</span> para todos los
subdominios y <span class="mono">localhost</span> para probar en tu computadora.</p>
<h3>3. Marca</h3>
<div class="row" style="align-items:flex-start"><div class="grow" style="min-width:260px">
<label for="nombre">Nombre que ve el cliente</label>
<input id="nombre" name="nombre" maxlength="60" required value="{esc(found.name)}">
<label for="color">Color de acento</label>
<div class="row"><input type="color" name="pick" aria-label="Elegir color"
 value="{esc(found.accent)}" style="width:54px;height:44px;padding:4px">
<input id="color" name="color" class="mono grow" maxlength="7" required
 pattern="#[0-9a-fA-F]{{6}}" value="{esc(found.accent)}"></div>
<label for="logo">Logo (PNG, JPG o WebP, cuadrado, hasta 200 KB)</label>
<input id="logo" name="logo" type="file" accept="image/png,image/jpeg,image/webp">{logo_note}
<label for="asistente">Nombre del asistente (opcional)</label>
<input id="asistente" name="asistente" maxlength="40" value="{esc(found.assistant)}"
 placeholder="Por ejemplo: Nina">
<label for="saludo">Saludo al abrir el chat (opcional)</label>
<textarea id="saludo" name="saludo" maxlength="300" style="min-height:70px"
 placeholder="{esc(widget.greeting(found.name, found.language))}">{esc(found.greeting)}</textarea>
<label for="sugerencias">Preguntas sugeridas (hasta 4, una por línea)</label>
<textarea id="sugerencias" name="sugerencias" style="min-height:96px"
 placeholder="¿Hacen envíos?">{esc(chr(10).join(found.suggestions))}</textarea>
<p class="muted small">Se muestran cuando la conversación está vacía.</p>
<button class="primary" type="submit">Guardar widget</button></div>
<div><label>Vista previa</label>
<div id="widget-preview" style="width:min(372px,calc(100vw - 66px));height:560px"></div>
<p class="muted small">Es el chat real de {esc(ctx.session.company)}: lo que escribas acá
aparece en Casos.</p></div></div></form></div>
<script src="/widget.js" data-chat="{esc(slug)}" data-mode="inline"
 data-target="#widget-preview"></script><script>{WIDGET_PREVIEW_JS}</script>"""


@router.post("/configuracion/widget")
async def save_widget(request: Request, data: Files, deps: Deps) -> Response:
    if data.too_big:
        return _back("deploy", error="El logo es demasiado grande.")
    ctx = enter(request, deps, data.fields)
    if isinstance(ctx, Response):
        return ctx
    fields = data.fields
    try:
        with deps.connection() as conn:
            widget.save(
                conn,
                ctx.tenant_id,
                name=fields.get("nombre", ""),
                accent=fields.get("color", ""),
                domains=fields.get("dominios", ""),
                suggestions=fields.get("sugerencias", ""),
                logo=data.files[0].data if data.files else None,
                remove_logo=fields.get("quitar_logo") == "si",
                assistant=fields.get("asistente", ""),
                greeting=fields.get("saludo", ""),
            )
    except widget.WidgetError as error:
        return _back("deploy", error=str(error))
    return _back("deploy", ok="Widget guardado.")


def _language_html(ctx: Ctx, language: str) -> str:
    """Idioma de la empresa (decisión 036): el del asistente, el chat, el widget y los emails."""
    options = "".join(
        f'<option value="{code}"{" selected" if code == language else ""}>{name}</option>'
        for code, name in i18n.NAMES.items()
    )
    return f"""<div class="card"><form method="post" action="/panel/configuracion/idioma">
{_csrf(ctx)}<label for="idioma">Idioma con el que atiende el asistente</label>
<div class="row"><select id="idioma" name="idioma" style="max-width:260px">{options}</select>
<button type="submit">Guardar idioma</button></div>
<p class="muted small">Cambia las respuestas del asistente, los textos del chat y del widget y el
email del código de verificación. En inglés, el asistente usa un tono cálido y profesional; en
español, rioplatense con voseo.</p></form></div>"""


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


def _voice_html(ctx: Ctx, found: brand_voice.Voice, language: str) -> str:
    """Voz de marca (decisión 081): se aplica en el prompt y la controla el supervisor."""
    # El trato es del español: en una empresa en inglés queda oculto (el mismo HTML en los dos
    # idiomas, decisión 037).
    options = "".join(
        f'<option value="{code}"{" selected" if found.trato == code else ""}>{esc(label)}'
        "</option>"
        for code, label in (("", "Según el idioma"), ("vos", "Vos"), ("tu", "Tú"),
                            ("usted", "Usted"))
    )  # fmt: skip
    treatment = (
        f"<div{' hidden' if language == 'en' else ''}>"
        '<label for="trato">Trato</label>'
        f'<select id="trato" name="trato" style="max-width:260px">{options}</select></div>'
    )
    checked = " checked" if found.emojis else ""
    company = ctx.session.company
    signature = f"— The {company} team" if english.english() else f"— El equipo de {company}"
    return f"""<div class="card"><form method="post" action="/panel/configuracion/voz">{_csrf(ctx)}
<label for="tono">Tono</label>
<input id="tono" name="tono" maxlength="{brand_voice.MAX_TONE}" value="{esc(found.tono)}"
 placeholder="Cálido y cercano, sin vueltas">{treatment}
<label for="usar">Palabras a usar (separadas por coma)</label>
<input id="usar" name="usar" value="{esc(", ".join(found.usar))}">
<label for="evitar">Palabras a evitar (separadas por coma)</label>
<input id="evitar" name="evitar" value="{esc(", ".join(found.evitar))}">
<label class="row" style="gap:8px"><input type="checkbox" name="emojis" value="si"{checked}
 style="width:auto"> <span>Usar emojis</span></label>
<label for="firma">Firma</label>
<input id="firma" name="firma" maxlength="{brand_voice.MAX_SIGNATURE}" value="{esc(found.firma)}"
 placeholder="{esc(signature)}">
<p class="muted small">Va en el prompt del asistente y la controla el supervisor de respuestas: una
palabra a evitar hace que la respuesta se reescriba. La firma se agrega al final de cada
respuesta y, sin emojis, se sacan antes de enviar.</p>
<button type="submit">Guardar voz de marca</button></form></div>"""


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


def _supervised_html(ctx: Ctx, on: bool) -> str:
    """Modo supervisado (decisión 072): apagado por defecto."""
    state = "Activado" if on else "Desactivado"
    return f"""<div class="card"><div class="row"><div class="grow">
<span class="badge {"resolved" if on else ""}">{state}</span>
<p class="muted small">Cada respuesta del asistente queda como borrador en Casos. Una persona del
equipo la envía, la edita o la rechaza antes de que llegue al cliente, que mientras tanto ve
“escribiendo…”. Las acciones con herramientas siguen pasando por el guardián y sus
aprobaciones.</p></div>
<form method="post" action="/panel/configuracion/supervisado">{_csrf(ctx)}
<button name="activo" value="{"no" if on else "si"}" class="{"" if on else "primary"}">
{"Desactivar" if on else "Activar"}</button></form></div></div>"""


def _answer_supervisor_html(ctx: Ctx, on: bool, found: dict[str, Any]) -> str:
    """Supervisor de respuestas (decisión 079): encendido por defecto, con lo que agregó."""
    state = "Activado" if on else "Desactivado"
    seconds = f"{found['avg_ms'] / 1000:.1f} s"
    return f"""<div class="card"><div class="row"><div class="grow">
<span class="badge {"resolved" if on else ""}">{state}</span>
<p class="muted small">Un modelo barato revisa cada respuesta con datos, políticas o acciones
antes de enviarla: que coincida con las fuentes y las herramientas, con las reglas y con el tono
de la marca. Puede aprobarla, pedir que se reescriba una vez o derivar a una persona.</p>
<table class="small"><thead><tr><th>Últimos 30 días</th><th></th></tr></thead><tbody>
<tr><td>Revisiones</td><td>{found["reviews"]}</td></tr>
<tr><td>Pidió reescribir</td><td>{found["rewrites"]}</td></tr>
<tr><td>Derivó</td><td>{found["escalations"]}</td></tr>
<tr><td>Latencia media que agrega</td><td>{seconds}</td></tr>
<tr><td>Costo total</td><td>US$ {found["cost_usd"]:.4f}</td></tr></tbody></table></div>
<form method="post" action="/panel/configuracion/supervisor">{_csrf(ctx)}
<button name="activo" value="{"no" if on else "si"}" class="{"" if on else "primary"}">
{"Desactivar" if on else "Activar"}</button></form></div></div>"""


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
a la derecha, cada paso del caso en vivo (intención, verificación, herramientas, reglas del
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


def _model_html(ctx: Ctx, conn: Any) -> str:
    """API key propia y tope de gasto mensual (decisión 049). La key nunca se muestra."""
    own = tenant_llm.own_provider(conn, ctx.tenant_id)
    limits = spending.budget(conn, ctx.tenant_id)
    spent = spending.month_spend(conn, ctx.tenant_id)
    used = spending.ratio(spent, limits)
    cap = (
        f'<span>Tope:</span> <span class="mono">USD {limits.cap_usd:.2f}</span>'
        if limits.cap_usd is not None
        else "<span>Sin tope</span>"
    )
    calls = (
        f' · <span>Llamadas al modelo:</span> <span class="mono">{spent.calls} / '
        f"{limits.cap_calls}</span>"
        if limits.cap_calls
        else ""
    )
    chosen = (
        f'<span class="mono">{esc(tenant_llm.PROVIDERS[own])}</span>'
        if own
        else "<span>La de la plataforma, con límites</span>"
    )
    options = "".join(
        f'<option value="{value}"{" selected" if value == (own or "plataforma") else ""}>'
        f"{esc(label)}</option>"
        for value, label in [
            ("plataforma", "Usar la de la plataforma"),
            *tenant_llm.PROVIDERS.items(),
        ]
    )
    tope = esc(str(spending._setting(conn, ctx.tenant_id) or ""))
    warn = ' style="color:#b45309"' if used >= spending.WARN_AT else ""
    return f"""<div class="card"><p><strong>Key en uso:</strong> {chosen}</p>
<p{warn}><strong>Gasto del mes:</strong> <span class="mono">USD {spent.usd:.2f} ·
{used:.0%}</span> · {cap}{calls}</p>
<p class="muted small">Al 80 % se envía un aviso; al 100 %, el asistente pasa cada mensaje a una
persona del equipo hasta el mes siguiente o hasta que subas el tope.</p>
<form method="post" action="/panel/configuracion/modelo">{_csrf(ctx)}
<label for="proveedor">Proveedor</label><select id="proveedor" name="proveedor"
style="max-width:320px">{options}</select>
<label for="clave">API key (se guarda cifrada; dejala vacía para conservar la guardada)</label>
<input id="clave" name="clave" type="password" autocomplete="off" style="max-width:420px">
<label for="tope">Tope mensual en USD (vacío: sin tope propio)</label>
<input id="tope" name="tope" inputmode="decimal" value="{tope}" style="max-width:160px">
<div class="row"><button type="submit">Guardar</button></div></form></div>"""


DAYS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


def _rules_html(ctx: Ctx, found: rules.Rules) -> str:
    """Horario del equipo y a quién derivar (decisión 051). Las reglas por herramienta están en
    cada herramienta, más abajo."""
    hours = found.hours
    days = "".join(
        f'<label style="font-weight:400"><input type="checkbox" name="dia_{n}" value="si"'
        f"{' checked' if hours and n in hours.days else ''}> {label}</label> "
        for n, label in enumerate(DAYS, start=1)
    )
    start = esc(hours.start if hours else "")
    end = esc(hours.end if hours else "")
    return f"""<div class="card"><form method="post" action="/panel/configuracion/reglas">
{_csrf(ctx)}<label>Días en que atiende el equipo</label><div class="row small">{days}</div>
<div class="row"><div><label for="desde">Desde</label>
<input id="desde" name="desde" type="time" value="{start}"></div>
<div><label for="hasta">Hasta</label><input id="hasta" name="hasta" type="time" value="{end}">
</div></div>
<p class="muted small">El asistente atiende siempre. Fuera de este horario, cuando pasa un caso a
una persona, le dice al cliente cuándo le responden. Sin días marcados, no se avisa nada.</p>
<label for="derivar_a">Email que recibe los casos derivados y las aprobaciones</label>
<input id="derivar_a" name="derivar_a" type="email" value="{esc(found.handoff_email or "")}"
 placeholder="soporte@tutienda.com" style="max-width:360px">
<p class="muted small">Vacío: el aviso va a los usuarios de la empresa.</p>
<button type="submit">Guardar reglas</button></form></div>"""


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


def _upsell_html(ctx: Ctx, found: list[upsell.Rule], kinds: list[str]) -> str:
    """Ventas cruzadas (decisión 061): qué producto ofrecer, cuándo y con qué descuento máximo.
    Las aplica el guardián: una oferta por conversación, nunca con el cliente enojado ni en un
    reclamo."""
    rows = "".join(
        f"<tr><td>{esc(r.product)}</td>"
        f"<td>{esc(', '.join(type_label(k) for k in r.case_types))}</td>"
        f'<td class="mono">{r.max_discount:g} %</td><td>'
        f'<form method="post" action="/panel/configuracion/ventas/{r.id}/borrar">{_csrf(ctx)}'
        '<button type="submit" class="secondary">Quitar</button></form></td></tr>'
        for r in found
    )
    table = (
        "<table><thead><tr><th>Producto</th><th>Cuándo</th><th>Descuento máximo</th><th></th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
        if found
        else '<p class="muted">Todavía no hay productos para ofrecer.</p>'
    )
    boxes = "".join(
        f'<label style="font-weight:400"><input type="checkbox" name="tipo_{esc(k)}" value="si">'
        f" {esc(type_label(k))}</label> "
        for k in kinds
    )
    form = (
        f"""<form method="post" action="/panel/configuracion/ventas">{_csrf(ctx)}
<label for="producto">Producto (con el nombre que tiene en tu tienda)</label>
<input id="producto" name="producto" style="max-width:360px">
<label>Cuándo ofrecerlo</label><div class="row small">{boxes}</div>
<label for="descuento">Descuento máximo (%)</label>
<input id="descuento" name="descuento" inputmode="decimal" value="0" style="max-width:120px">
<button type="submit">Agregar</button></form>"""
        if kinds
        else '<p class="muted small">No hay tipos de caso activos donde ofrecer algo.</p>'
    )
    return f"""<div class="card">{table}
<p class="muted small">El asistente ofrece como mucho un producto por conversación, después de
resolver lo que pidió el cliente, y nunca si está enojado o en medio de un reclamo. El descuento
no pasa el máximo.</p>{form}</div>"""


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


@router.post("/configuracion/modelo")
def save_model(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    choice = form.get("proveedor", "")
    key = form.get("clave", "").strip()
    raw = form.get("tope", "").strip().replace(",", ".")
    try:
        cap = Decimal(raw) if raw else None
    except InvalidOperation:
        return _back("settings/usage", error="El tope tiene que ser un número.")
    if cap is not None and cap < 0:
        return _back("settings/usage", error="El tope no puede ser negativo.")
    with deps.connection() as conn:
        if choice == "plataforma":
            tenant_llm.clear_key(conn, ctx.tenant_id)
        elif choice in tenant_llm.PROVIDERS:
            if key:
                try:
                    tenant_llm.save_key(conn, ctx.tenant_id, choice, key)
                except ValueError as error:
                    return _back("settings/usage", error=str(error))
            elif tenant_llm.own_provider(conn, ctx.tenant_id) != choice:
                return _back("settings/usage", error="Falta la API key de ese proveedor.")
        else:
            return _back("settings/usage", error="Elegí un proveedor de la lista.")
        spending.set_budget(conn, ctx.tenant_id, cap)
    return _back("settings/usage", ok="Modelo y tope guardados.")


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
        attention = _rules_html(ctx, rules.load(conn, ctx.tenant_id))
        supervised = _supervised_html(ctx, supervision.enabled(conn, ctx.tenant_id))
        reviewer = _answer_supervisor_html(
            ctx,
            answer_supervisor.enabled(conn, ctx.tenant_id),
            answer_supervisor.stats(conn, ctx.tenant_id),
        )
    body = f"""{head("Rules", LEADS["Rules"])}
<h2>Business hours and hand-offs</h2>{attention}
<h2>Review before sending</h2>{supervised}
<h2>Answer check</h2>{reviewer}"""
    return _section(request, ctx, "agent", "Rules", body)


@router.get("/agent/actions", response_class=HTMLResponse)
def agent_actions_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        tools = review.tools(conn, ctx.tenant_id)
    body = f"""{head("Actions", LEADS["Actions"])}
{_tools_html(ctx, tools)}"""
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
    body = f"""{head("Topics", LEADS["Topics"])}
{_types_html(ctx, types, approved)}"""
    return _section(request, ctx, "agent/topics", "Topics", body)


@router.get("/agent/voice", response_class=HTMLResponse)
def agent_voice_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        language = i18n.tenant_language(conn, ctx.tenant_id)
        voice_found = brand_voice.load(conn, ctx.tenant_id)
    body = f"""{head("Brand & voice", LEADS["Brand & voice"])}
{_voice_html(ctx, voice_found, language)}"""
    return _section(request, ctx, "agent/voice", "Brand & voice", body)


@router.get("/agent/sales", response_class=HTMLResponse)
def agent_sales_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        offers = _upsell_html(
            ctx, upsell.load(conn, ctx.tenant_id), upsell.eligible_types(conn, ctx.tenant_id)
        )
    body = f"""{head("Sales", LEADS["Sales"])}
{offers}"""
    return _section(request, ctx, "agent/sales", "Sales", body)


@router.get("/agent/language", response_class=HTMLResponse)
def agent_language_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        language = i18n.tenant_language(conn, ctx.tenant_id)
    body = f"""{head("Language", LEADS["Language"])}
{_language_html(ctx, language)}"""
    return _section(request, ctx, "agent/language", "Language", body)


@router.get("/deploy", response_class=HTMLResponse)
def deploy_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = system.load(conn, ctx.tenant_id)
        mode = system.mode_for(conn, ctx.tenant_id)
        slug = chat_slug(conn, ctx.tenant_id)
        brand = widget.load(conn, ctx.tenant_id)
        whatsapp_setup = whatsapp.load(conn, ctx.tenant_id)
    site = (
        _widget_html(ctx, request, slug, brand)
        if slug
        else '<div class="card empty">This company has no web chat.</div>'
    )
    body = f"""{head("Deploy", LEADS["Deploy"])}
<h2>Connect your store</h2>{_system_html(ctx, found, mode)}
<h2 id="widget">Add the chat to your website</h2>{site}
<h2>WhatsApp</h2>{_whatsapp_html(ctx, request, whatsapp_setup)}"""
    return _section(request, ctx, "deploy", "Deploy", body)


@router.get("/settings/usage", response_class=HTMLResponse)
def settings_usage_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        model = _model_html(ctx, conn)
    body = f"""{head("Usage & model", LEADS["Usage & model"])}
{model}"""
    return _section(request, ctx, "settings/usage", "Usage & model", body)


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
<form method="post" action="/panel/settings/data/delete">{_csrf(ctx)}
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
        return _not_found("Ese tipo de caso", ctx.session)
    return _back("agent/topics", ok="Tipo de caso actualizado.")


@router.post("/configuracion/herramientas/{tool_id}")
def update_tool(tool_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    approved = {"si": True, "no": False}.get(form.get("aprobada", ""))
    limits = form.get("limites") == "si"
    # Reglas por herramienta (decisión 051): casillas sin marcar no llegan en el formulario.
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
        return _not_found("Esa herramienta", ctx.session)
    return _back("agent/actions", ok="Herramienta actualizada.")


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
            result = system.test_connection(conn, ctx.tenant_id, client_factory=deps.client_factory)
    except system.ConnectionError_ as error:
        return _back("deploy", error=str(error))
    if result["ok"]:
        return _back("deploy", ok="La prueba de conexión pasó. Ya podés activar.")
    return _back("deploy", error=f"La prueba de conexión falló: {result['detalle']}")


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
    except (ValueError, httpx.HTTPError) as error:
        detail = str(error) if isinstance(error, ValueError) else "Shopify no respondió."
        return _back("deploy", error=detail)
    return _back(
        "configuracion",
        ok="Tienda conectada. Probá la conexión y aprobá sus herramientas.",
    )


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
    response = _back(
        "configuracion", ok="Tienda conectada. Probá la conexión y aprobá sus herramientas."
    )
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH)
    return response


@router.post("/configuracion/sistema/herramientas")
def probe_system_tools(request: Request, form: Form, deps: Deps) -> Response:
    """Prueba cada herramienta de solo lectura con un pedido de ejemplo (decisión 057)."""
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
    summary = " · ".join(f"{tool_label(r['herramienta'])}: {r['detalle']}" for r in results)
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
    return _back("deploy", ok="Listo: el asistente ya usa tu sistema real.")


@router.post("/configuracion/sistema/desactivar")
def deactivate_system(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        system.deactivate(conn, ctx.tenant_id)
    return _back("deploy", ok="El asistente volvió al modo simulado.")


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
        return "WhatsApp activo: el asistente ya responde en tu número."

    return _whatsapp_action(request, form, deps, run)


@router.post("/configuracion/whatsapp/desactivar")
def deactivate_whatsapp(request: Request, form: Form, deps: Deps) -> Response:
    def run(conn: Any, tenant_id: uuid.UUID) -> str:
        whatsapp.deactivate(conn, tenant_id)
        return "WhatsApp desactivado: el asistente ya no responde en tu número."

    return _whatsapp_action(request, form, deps, run)
