"""Panel, pantallas del hito 6 (decisión 026): alta de empresas, onboarding sin comandos,
revisión de tipos de caso y herramientas, y conexión con el sistema de la empresa.

Todas las rutas entran por `enter` (app/panel/routes.py): la empresa sale de la sesión y cada
consulta corre dentro de `tenant_scope`, así que un id de otra empresa responde 404 igual que
uno que no existe. Los POST exigen el token anti-CSRF.
"""

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.companies import connection as system
from app.companies import delete as removal
from app.companies import onboarding, review
from app.companies.create import CompanyError, chat_slug, create_company
from app.onboarding.ingest import Upload
from app.panel import auth
from app.panel.html import esc, local_time, page
from app.panel.labels import money, tool_label, type_label
from app.panel.routes import Ctx, Deps, Form, PanelDeps, _forbidden, _not_found, _user_id, enter

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
        f'<td><a href="/panel/empresas/{tenant_id}/borrar">Borrar…</a></td></tr>'
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
    return HTMLResponse(page("Empresas", body, active="empresas", session=ctx.session))


@router.get("/empresas", response_class=HTMLResponse)
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
        return _back("empresas", error=str(error))
    created = f"""<div class="card okmsg"><b>{esc(company.name)} quedó creada.</b>
<p>Para entrar a su panel: cerrá esta sesión e ingresá con el email y la clave que cargaste.
La clave no se vuelve a mostrar.</p>
<p>Su chat de prueba: <span class="mono">/c/{esc(company.chat_slug)}</span></p></div>"""
    return _companies_page(ctx, deps, "", created)


# -- onboarding ------------------------------------------------------------------------------
@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = onboarding.sources(conn, ctx.tenant_id)
        names = sorted(onboarding.business_names(conn, ctx.tenant_id) - {ctx.session.company})
        run = onboarding.state(conn, ctx.tenant_id)
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
        '<a href="/panel/reporte">reporte de preparación</a> y después revisá y aprobá los tipos '
        'de caso y las herramientas en <a href="/panel/configuracion">Configuración</a>.</div>'
        if run is not None and run.status == "done" and not running
        else ""
    )
    body = f"""<h1>Onboarding</h1><p class="lead">Subí el historial de atención y los documentos
de {esc(ctx.session.company)}. El asistente aprende de ahí: detecta los tipos de caso, arma sus
pruebas y te dice qué parte de la atención resolvería solo.</p>{_notice(request)}{done}
<div class="card"><h2 style="margin-top:0">1. Subí tus fuentes</h2>{sources_table}
<form method="post" action="/panel/onboarding/fuentes" enctype="multipart/form-data"
 style="margin-top:12px">{_csrf(ctx)}
<label for="archivos">Historial de chats y documentos</label>
<input id="archivos" name="archivos" type="file" multiple required
 accept=".txt,.zip,.csv,.json,.md,.docx,.pdf">
<p class="muted small">Chats de WhatsApp exportados (.txt, o un .zip con varios), tickets de
helpdesk (.csv o .json) y documentos de políticas (.md, .txt, .docx o .pdf con texto). Hasta
50 MB. En los chats y tickets, los datos personales se reemplazan por marcadores antes de
guardar. El archivo original no se conserva.</p>
<button class="primary" type="submit">Subir</button></form>
<form method="post" action="/panel/onboarding/nombres" style="margin-top:16px">{_csrf(ctx)}
<label for="nombres">Con qué nombre figura la empresa en los chats de WhatsApp</label>
<input id="nombres" name="nombres" value="{esc(", ".join(names))}"
 placeholder="Por ejemplo: Soporte Tienda, Ana de Tienda">
<p class="muted small">Sirve para saber qué mensajes son del equipo y cuáles del cliente.
Separá los nombres con comas y cargalos antes de subir los chats.</p>
<button type="submit">Guardar nombres</button></form></div>
<div class="card"><h2 style="margin-top:0">2. Análisis</h2>
<ol class="steps">{steps}</ol><div style="margin-top:12px">{action}</div></div>"""
    return HTMLResponse(
        page(
            "Onboarding",
            body,
            active="onboarding",
            session=ctx.session,
            refresh=8 if running else None,
        )
    )


def _ingest(deps: PanelDeps, tenant_id: uuid.UUID, files: list[Upload]) -> Any:
    with deps.connection() as conn:
        return onboarding.add_sources(conn, tenant_id, files, deps.embedder(conn))


@router.post("/onboarding/fuentes")
async def upload_sources(request: Request, data: Files, deps: Deps) -> Response:
    if data.too_big:
        return _back("onboarding", error="El envío supera los 50 MB.")
    ctx = enter(request, deps, data.fields)
    if isinstance(ctx, Response):
        return ctx
    if not data.files:
        return _back("onboarding", error="Elegí al menos un archivo.")
    try:
        result = await run_in_threadpool(_ingest, deps, ctx.tenant_id, data.files)
    except onboarding.OnboardingError as error:
        return _back("onboarding", error=str(error))
    summary = (
        f"Subiste {result.sources} archivos: {result.conversations} conversaciones y "
        f"{result.documents} documentos."
    )
    if result.skipped:
        summary += f" {result.skipped} ya estaban subidos."
    # Un documento sin texto (PDF escaneado) se avisa acá: el resto de la carga ya quedó.
    warning = " ".join(result.unreadable)
    if not result.sources and not result.skipped:
        return _back("onboarding", warning=warning) if warning else _back("onboarding", ok=summary)
    return _back("onboarding", ok=summary, warning=warning)


@router.post("/onboarding/nombres")
def save_business_names(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        onboarding.set_business_names(conn, ctx.tenant_id, form.get("nombres", "").split(","))
    return _back("onboarding", ok="Nombres guardados.")


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
        return _back("onboarding", error=str(error))
    deps.kick_worker()  # con la corrida ya confirmada en la cola
    return _back("onboarding", ok="El análisis empezó.")


ONLY_OPERATOR_DELETES = "Solo el operador de la plataforma puede borrar una empresa."


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
    body = f"""<p><a href="/panel/empresas">← Empresas</a></p>
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
<a class="btn" href="/panel/empresas">Cancelar</a></p></form></div>"""
    return HTMLResponse(page("Borrar empresa", body, active="empresas", session=ctx.session))


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
        return _back("empresas", error=str(error))
    total = sum(deleted.values())
    return _back(
        "empresas", ok=f"La empresa quedó borrada: {total} filas en {len(deleted)} tablas."
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
            '<a href="/panel/onboarding">onboarding</a>.</p>'
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
        if m.get("max_amount") is not None:
            facts.append(f"tope {money(m['max_amount'])}")
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
{"Quitar aprobación" if tool.approved else "Aprobar"}</button></form></div>{limits}</div>"""
        )
    return "".join(cards)


def _plain(value: Any) -> str:
    if value is None:
        return ""
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


def _system_html(ctx: Ctx, found: system.SystemConnection | None, mode: str) -> str:
    badge, label = MODES[mode]
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
    return f"""<div class="card"><span class="badge {badge}">{label}</span>
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
<div class="row">{probe}{activate}</div>{test}</div>"""


@router.get("/configuracion", response_class=HTMLResponse)
def settings_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        types = review.case_types(conn, ctx.tenant_id)
        tools = review.tools(conn, ctx.tenant_id)
        found = system.load(conn, ctx.tenant_id)
        mode = system.mode_for(conn, ctx.tenant_id)
        slug = chat_slug(conn, ctx.tenant_id)
    approved = {t.name for t in tools if t.approved}
    chat = (
        f"<p>Probá el asistente de {esc(ctx.session.company)} en su propio chat: "
        f'<a href="/c/{esc(slug)}" target="_blank" rel="noopener">/c/{esc(slug)}</a></p>'
        '<p class="muted small">Usa los tipos de caso activos, las herramientas aprobadas y tus '
        "documentos. Las conversaciones aparecen en Casos.</p>"
        if slug and not ctx.session.admin
        else '<p>El chat de la demo está en <a href="/" target="_blank" rel="noopener">la '
        "página principal</a>.</p>"
        if ctx.session.admin
        else '<p class="muted">Esta empresa no tiene un chat web.</p>'
    )
    body = f"""<h1>Configuración</h1><p class="lead">Revisá lo que encontró el análisis y decidí
qué usa el asistente. Lo que activás o aprobás acá pasa al asistente en vivo en el momento; nada
queda activo sin tu aprobación.</p>{_notice(request)}
<h2>Tipos de caso</h2>{_types_html(ctx, types, approved)}
<h2>Herramientas</h2>{_tools_html(ctx, tools)}
<h2>Sistema de la empresa</h2>{_system_html(ctx, found, mode)}
<h2>Chat de prueba</h2><div class="card">{chat}</div>"""
    return HTMLResponse(page("Configuración", body, active="configuracion", session=ctx.session))


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
        return _back("configuracion", error=str(error))
    if not found:
        return _not_found("Ese tipo de caso", ctx.session)
    return _back("configuracion", ok="Tipo de caso actualizado.")


@router.post("/configuracion/herramientas/{tool_id}")
def update_tool(tool_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    approved = {"si": True, "no": False}.get(form.get("aprobada", ""))
    limits = form.get("limites") == "si"
    try:
        with deps.connection() as conn:
            found = review.set_tool(
                conn,
                ctx.tenant_id,
                tool_id,
                approved=approved,
                max_amount=form.get("monto_maximo", "") if limits else review.UNSET,
                approval_over=form.get("aprobacion_sobre", "") if limits else review.UNSET,
            )
    except review.ReviewError as error:
        return _back("configuracion", error=str(error))
    if not found:
        return _not_found("Esa herramienta", ctx.session)
    return _back("configuracion", ok="Herramienta actualizada.")


@router.post("/configuracion/sistema/openapi")
async def save_openapi(request: Request, data: Files, deps: Deps) -> Response:
    if data.too_big:
        return _back("configuracion", error="El archivo es demasiado grande.")
    ctx = enter(request, deps, data.fields)
    if isinstance(ctx, Response):
        return ctx
    text = (
        data.files[0].data.decode("utf-8-sig", errors="replace")
        if data.files
        else data.fields.get("texto", "")
    )
    if not text.strip():
        return _back("configuracion", error="Subí o pegá el OpenAPI.")
    try:
        with deps.connection() as conn:
            found = system.save_openapi(conn, ctx.tenant_id, text)
    except system.ConnectionError_ as error:
        return _back("configuracion", error=str(error))
    return _back("configuracion", ok=f"OpenAPI guardado: {found.operations} operaciones.")


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
        return _back("configuracion", error=str(error))
    return _back("configuracion", ok="Acceso guardado. Ahora probá la conexión.")


@router.post("/configuracion/sistema/probar")
def test_system(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            result = system.test_connection(conn, ctx.tenant_id, client_factory=deps.client_factory)
    except system.ConnectionError_ as error:
        return _back("configuracion", error=str(error))
    if result["ok"]:
        return _back("configuracion", ok="La prueba de conexión pasó. Ya podés activar.")
    return _back("configuracion", error=f"La prueba de conexión falló: {result['detalle']}")


@router.post("/configuracion/sistema/activar")
def activate_system(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            system.activate(conn, ctx.tenant_id)
    except system.ConnectionError_ as error:
        return _back("configuracion", error=str(error))
    return _back("configuracion", ok="Listo: el asistente ya usa tu sistema real.")


@router.post("/configuracion/sistema/desactivar")
def deactivate_system(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        system.deactivate(conn, ctx.tenant_id)
    return _back("configuracion", ok="El asistente volvió al modo simulado.")
