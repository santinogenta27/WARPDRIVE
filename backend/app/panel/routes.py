"""Rutas del panel de la empresa (decisión 022): login, aprobaciones, bandeja de casos, detalle
con la auditoría y reporte de preparación del onboarding.

Páginas HTML servidas por la misma API (entra en el servicio gratis de Render). Los formularios
son `application/x-www-form-urlencoded` y se leen a mano (sin python-multipart). Cada POST que
cambia algo exige la sesión y el token anti-CSRF del formulario.
"""

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import parse_qs

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.audit import log as audit_log
from app.cases.closing import close_inactive
from app.db.connection import connect, tenant_scope
from app.panel import auth
from app.panel.html import RESOLVED_BY, esc, local_time, page, status_badge
from app.panel.labels import type_label
from app.panel.notify import notify_customer
from app.policy.approvals import ApprovalError, ApprovalOutcome, decide
from app.policy.guardian import Guardian

router = APIRouter(prefix="/panel", include_in_schema=False)

REPORT_TENANT = "Acme Store (onboarding)"
MAX_REPLY = 2000


@dataclass
class PanelDeps:
    connection: Callable[[], AbstractContextManager[psycopg.Connection]]
    tenant_id: uuid.UUID | None  # la empresa del panel (Acme en la demo)
    guardian: Callable[[psycopg.Connection], Guardian]
    report_tenant: uuid.UUID | None = None  # de dónde sale el reporte de preparación


@contextmanager
def _transaction() -> Iterator[psycopg.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _production_deps() -> PanelDeps:
    from app.config import load_dotenv
    from app.demo.acme import find_demo_tenant, shared_acme
    from app.tools.runtime import ToolRuntime

    load_dotenv()
    with _transaction() as conn:
        tenant_id = find_demo_tenant(conn)
        row = conn.execute("select id from tenants where name = %s", (REPORT_TENANT,)).fetchone()
    return PanelDeps(
        connection=_transaction,
        tenant_id=tenant_id,
        # La misma API simulada que usa el agente del chat: lo aprobado se ve en el pedido.
        guardian=lambda conn: Guardian(conn, ToolRuntime(shared_acme())),
        report_tenant=row[0] if row else None,
    )


def panel_deps() -> PanelDeps:
    return _production_deps()


Deps = Annotated[PanelDeps, Depends(panel_deps)]


async def form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


Form = Annotated[dict[str, str], Depends(form_data)]


def _session(request: Request) -> tuple[str, str] | None:
    """(token, email) de la sesión del panel, o None."""
    token = request.cookies.get(auth.COOKIE)
    email = auth.read_session(token) if auth.configured() else None
    return (token, email) if token and email else None


def _to_login() -> RedirectResponse:
    return RedirectResponse("/panel/login", status_code=303)


def _forbidden() -> HTMLResponse:
    body = '<div class="card empty">El formulario venció. Volvé a cargar la página.</div>'
    return HTMLResponse(page("Sin permiso", body), status_code=403)


def _no_tenant() -> HTMLResponse:
    body = '<div class="card empty">La empresa de la demo todavía no está cargada.</div>'
    return HTMLResponse(page("Panel", body), status_code=503)


def _money(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return esc(value)
    return "$" + f"{amount:,.0f}".replace(",", ".")


# -- login -----------------------------------------------------------------------------------
def _login_page(error: str = "") -> str:
    note = (
        ""
        if auth.configured()
        else '<p class="muted small">Falta configurar PANEL_EMAIL y PANEL_PASSWORD.</p>'
    )
    alert = f'<p style="color:var(--bad)">{esc(error)}</p>' if error else ""
    body = f"""<div class="card" style="max-width:400px;margin:40px auto">
<h1>Ingresar al panel</h1><p class="lead">Equipo de atención de Acme Store.</p>{alert}{note}
<form method="post" action="/panel/login">
<label for="email">Email</label><input id="email" name="email" type="email" required
 autocomplete="username">
<label for="password">Clave</label><input id="password" name="password" type="password" required
 autocomplete="current-password">
<p><button class="primary" type="submit" style="width:100%">Ingresar</button></p></form></div>"""
    return page("Ingresar", body, logged_in=False)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    return RedirectResponse("/panel/casos" if _session(request) else "/panel/login", 303)


@router.get("/login", response_class=HTMLResponse)
def login_form() -> HTMLResponse:
    return HTMLResponse(_login_page())


@router.post("/login")
def login(request: Request, form: Form) -> Response:
    email, password = form.get("email", ""), form.get("password", "")
    if not auth.configured() or not auth.check_credentials(email, password):
        return HTMLResponse(_login_page("Email o clave incorrectos."), status_code=401)
    response = RedirectResponse("/panel/casos", status_code=303)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        auth.COOKIE,
        auth.new_session(email.strip().lower()),
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/panel",
    )
    return response


@router.post("/logout")
def logout() -> Response:
    response = _to_login()
    response.delete_cookie(auth.COOKIE, path="/panel")
    return response


def _user_id(conn: psycopg.Connection, tenant_id: uuid.UUID, email: str) -> uuid.UUID:
    """Usuario del panel en la tabla users (para `decided_by` de las aprobaciones)."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into users (tenant_id, email, role) values (%s, %s, 'owner')"
            " on conflict (tenant_id, email) do update set email = excluded.email returning id",
            (tenant_id, email),
        ).fetchone()
    assert row is not None
    user_id: uuid.UUID = row[0]
    return user_id


# -- aprobaciones ----------------------------------------------------------------------------
def _params(params: dict[str, Any]) -> str:
    parts = []
    for key, value in params.items():
        shown = _money(value) if key in ("amount", "monto") else esc(value)
        parts.append(f'<span class="muted">{esc(key)}</span> <b>{shown}</b>')
    return " · ".join(parts)


def _approval_cards(rows: list[tuple[Any, ...]], csrf: str, *, back: str) -> str:
    cards = []
    for approval_id, tool, params, case_id, created, first_message in rows:
        cards.append(
            f"""<div class="card"><div class="row"><div class="grow">
<span class="badge waiting_approval">Pendiente</span> <b class="mono">{esc(tool)}</b>
<div style="margin-top:6px">{_params(params)}</div>
<div class="muted small" style="margin-top:6px">“{esc(first_message or "")}” ·
<a href="/panel/casos/{case_id}">ver caso</a> · {local_time(created)}</div></div>
<form method="post" action="/panel/aprobaciones/{approval_id}" class="row">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="volver" value="{back}">
<button class="danger" name="decision" value="rechazar">Rechazar</button>
<button class="primary" name="decision" value="aprobar">Aprobar</button></form></div></div>"""
        )
    return "".join(cards)


def _pending(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID | None = None
) -> list[tuple[Any, ...]]:
    with tenant_scope(conn, tenant_id):
        return conn.execute(
            "select a.id, t.name, a.params, a.case_id, a.created_at,"
            " (select m.text from case_messages m where m.case_id = a.case_id"
            "  and m.role = 'customer' order by m.created_at desc limit 1)"
            " from approvals a join tools t on t.id = a.tool_id"
            " where a.status = 'pending' and (%s::uuid is null or a.case_id = %s)"
            " order by a.created_at",
            (case_id, case_id),
        ).fetchall()


@router.get("/aprobaciones", response_class=HTMLResponse)
def approvals_page(request: Request, deps: Deps) -> Response:
    session = _session(request)
    if session is None:
        return _to_login()
    if deps.tenant_id is None:
        return _no_tenant()
    with deps.connection() as conn:
        pending = _pending(conn, deps.tenant_id)
        with tenant_scope(conn, deps.tenant_id):
            decided = conn.execute(
                "select t.name, a.params, a.status, a.decided_at, a.case_id, u.email"
                " from approvals a join tools t on t.id = a.tool_id"
                " left join users u on u.id = a.decided_by"
                " where a.status <> 'pending' order by a.decided_at desc nulls last limit 10"
            ).fetchall()
    csrf = auth.csrf_token(session[0])
    cards = _approval_cards(pending, csrf, back="/panel/aprobaciones") or (
        '<div class="card empty">No hay pedidos esperando aprobación.</div>'
    )
    history = "".join(
        f"""<div class="card row"><span class="badge {
            "resolved" if status == "approved" else "escalated"
        }">{"Aprobado" if status == "approved" else "Rechazado"}</span>
<span class="mono">{esc(tool)}</span><span class="grow">{_params(params)}</span>
<span class="muted small">{esc(email or "")} · {local_time(decided)}</span>
<a href="/panel/casos/{case_id}">ver caso</a></div>"""
        for tool, params, status, decided, case_id, email in decided
        if decided is not None
    )
    body = f"""<h1>Aprobaciones</h1><p class="lead">Las acciones de riesgo alto (como un reembolso)
no se ejecutan hasta que una persona las aprueba. Al aprobar, se ejecuta por el guardián y el
cliente recibe el aviso en el chat.</p>{cards}
{f"<h2>Decididas</h2>{history}" if history else ""}"""
    return HTMLResponse(page("Aprobaciones", body, active="aprobaciones"))


def _approval_message(tool: str, params: dict[str, Any], outcome: ApprovalOutcome) -> str:
    order = params.get("order_id")
    about = f" del pedido {order}" if order else ""
    if outcome.status == "rejected":
        return (
            f"Revisamos tu solicitud{about} y no la pudimos aprobar. Si querés, respondé este "
            "mensaje y una persona del equipo te ayuda."
        )
    if outcome.status != "executed":
        return (
            f"Aprobamos tu solicitud{about}, pero no pudimos completarla automáticamente. "
            "Una persona del equipo te va a escribir para terminarla."
        )
    if tool == "iniciar_reembolso":
        amount = f" por {_money(params['amount'])}" if params.get("amount") else ""
        return (
            f"¡Buenas noticias! Aprobamos el reembolso{about}{amount}. Ya lo iniciamos: te "
            "avisamos cuando se acredite en tu medio de pago."
        )
    return f"¡Listo! Aprobamos y realizamos lo que pediste{about}."


@router.post("/aprobaciones/{approval_id}")
def decide_approval(approval_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    session = _session(request)
    if session is None:
        return _to_login()
    if not auth.valid_csrf(session[0], form.get("csrf", "")):
        return _forbidden()
    if deps.tenant_id is None:
        return _no_tenant()
    approve = form.get("decision") == "aprobar"
    back = form.get("volver", "")
    with deps.connection() as conn:
        with tenant_scope(conn, deps.tenant_id):
            row = conn.execute(
                "select a.case_id, t.name, a.params from approvals a"
                " join tools t on t.id = a.tool_id where a.id = %s",
                (approval_id,),
            ).fetchone()
        if row is None:
            body = '<div class="card empty">Esa aprobación no existe.</div>'
            return HTMLResponse(page("Aprobaciones", body), status_code=404)
        case_id, tool, params = row
        user_id = _user_id(conn, deps.tenant_id, session[1])
        try:
            outcome = decide(
                conn,
                deps.guardian(conn),
                deps.tenant_id,
                approval_id,
                approve=approve,
                decided_by=user_id,
            )
        except ApprovalError:
            # Ya decidida (doble clic o reenvío): no se ejecuta ni se avisa de nuevo.
            return RedirectResponse("/panel/aprobaciones", status_code=303)
        notify_customer(
            conn,
            deps.tenant_id,
            case_id,
            _approval_message(tool, params, outcome),
            kind="approval_decided",
            detail={
                "approval_id": str(approval_id),
                "decision": "aprobar" if approve else "rechazar",
                "status": outcome.status,
                "decided_by": str(user_id),
            },
        )
    target = back if back.startswith("/panel/") else "/panel/aprobaciones"
    return RedirectResponse(target, status_code=303)


# -- bandeja ---------------------------------------------------------------------------------
CHANNELS = {"web": "Chat web", "twilio_whatsapp": "WhatsApp", "whatsapp": "WhatsApp"}


@router.get("/casos", response_class=HTMLResponse)
def inbox(request: Request, deps: Deps) -> Response:
    if _session(request) is None:
        return _to_login()
    if deps.tenant_id is None:
        return _no_tenant()
    with deps.connection() as conn:
        # Regla de cierre: los que llevan 24 h sin respuesta del cliente quedan resueltos.
        close_inactive(conn, deps.tenant_id)
        rows = _inbox_rows(conn, deps.tenant_id)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[1]] = counts.get(row[1], 0) + 1
    summary = "".join(
        f'<div class="kpi"><div class="n">{counts.get(key, 0)}</div>'
        f'<div class="muted">{label}</div></div>'
        for key, label in (
            ("escalated", "Derivados a una persona"),
            ("waiting_approval", "Esperando aprobación"),
            ("open", "Abiertos"),
            ("resolved", "Resueltos"),
        )
    )
    items = (
        "".join(
            f"""<a class="card row" href="/panel/casos/{case_id}" style="text-decoration:none;
color:inherit"><div class="grow"><div class="row" style="gap:8px">
{status_badge(status, resolved_by)}
<span class="muted small">{esc(type_label(type_name))}</span>
<span class="muted small">· {esc(CHANNELS.get(channel or "", "Canal"))}
{" · verificado" if verified else ""}</span></div>
<div class="ellipsis" style="margin-top:6px">{esc(preview or "(sin texto)")}</div></div>
<div class="muted small" style="text-align:right">{local_time(updated)}<br>{total} mensajes
</div></a>"""
            for (
                case_id,
                status,
                updated,
                type_name,
                preview,
                total,
                channel,
                verified,
                resolved_by,
            ) in rows
        )
        or '<div class="card empty">Todavía no hay conversaciones.</div>'
    )
    body = f"""<h1>Casos</h1><p class="lead">Conversaciones con su estado: primero las derivadas
a una persona, después las que esperan una aprobación. Un caso se resuelve cuando el cliente
confirma o pasan 24 h sin respuesta.</p><div class="kpis">{summary}</div>
{items}"""
    return HTMLResponse(page("Casos", body, active="casos"))


def _inbox_rows(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[Any, ...]]:
    with tenant_scope(conn, tenant_id):
        return conn.execute(
            "select c.id, c.status, c.updated_at, t.name,"
            " (select m.text from case_messages m where m.case_id = c.id and m.role = 'customer'"
            "  order by m.created_at desc limit 1),"
            " (select count(*) from case_messages m where m.case_id = c.id),"
            " (select e.payload ->> 'channel' from audit_events e where e.case_id = c.id"
            "  and e.payload ? 'channel' limit 1),"
            " coalesce(k.verified_until > now(), false), c.resolved_by"
            " from cases c join contacts k on k.id = c.contact_id"
            " left join case_types t on t.id = c.case_type_id"
            " order by case c.status when 'escalated' then 0 when 'waiting_approval' then 1"
            " when 'open' then 2 else 3 end, c.updated_at desc limit 100"
        ).fetchall()


# -- detalle ---------------------------------------------------------------------------------
ACTIONS = {
    "responder": "Respondió",
    "ejecutar": "Actuó en el sistema y respondió",
    "pedir_aprobacion": "Pidió aprobación a una persona",
    "derivar": "Le avisó al cliente que lo atiende una persona",
}
VERIFICATION = {
    "verification_code_sent": "Pidió el email de la compra y envió un código",
    "verified": "Código correcto: identidad verificada",
    "wrong": "Código incorrecto",
    "locked": "Demasiados intentos: verificación bloqueada",
    "expired": "Código vencido",
}
TOOL_STATUS = {
    "executed": "ejecutada",
    "denied": "negada por el guardián",
    "approval_required": "necesita aprobación",
    "failed": "falló",
}


def _describe(kind: str, p: dict[str, Any]) -> tuple[str, str]:
    """(título, detalle en HTML) de un evento de auditoría."""
    if kind == "message_received":
        verified = "verificado" if p.get("verified") else "sin verificar"
        return (
            "Mensaje del cliente",
            f"“{esc(p.get('text'))}” <span class=muted>· {verified}</span>",
        )
    if kind == "model_call":
        tokens = int(p.get("input_tokens") or 0) + int(p.get("output_tokens") or 0)
        return "Consultó al modelo", (
            f"{esc(p.get('purpose'))} · <span class=mono>{esc(p.get('model'))}</span> · "
            f"{tokens} tokens · USD {esc(p.get('cost_usd'))}"
        )
    if kind == "classified":
        return "Clasificó el caso", esc(type_label(p.get("case_type")))
    if kind == "rag_search":
        sources = sorted(set(p.get("sources") or []))
        shown = ", ".join(f"<span class=mono>{esc(s)}</span>" for s in sources) or "sin resultados"
        return "Buscó en los documentos", f"Fuentes: {shown}"
    if kind == "tool_call":
        status = TOOL_STATUS.get(str(p.get("status")), esc(p.get("status")))
        reason = f" · {esc(p.get('reason'))}" if p.get("reason") else ""
        return "Usó una herramienta", (
            f"<span class=mono>{esc(p.get('tool'))}</span> "
            f"<span class=mono>{esc(json.dumps(p.get('input'), ensure_ascii=False))}</span>"
            f" → {status}{reason}"
        )
    if kind == "grounding_rejected":
        return "Descartó una respuesta sin respaldo", esc(p.get("reason"))
    if kind == "final_action":
        named = ", ".join(esc(s) for s in p.get("sources") or [])
        cited = f"<br><span class=muted>Fuentes citadas: {named}</span>" if named else ""
        title = ACTIONS.get(str(p.get("action")), str(p.get("action")))
        return title, f"“{esc(p.get('message'))}”{cited}"
    if kind == "escalated":
        return "Derivó a una persona", f"Motivo: {esc(p.get('motivo'))}"
    if kind == "human_reply":
        return "Respondió una persona del equipo", f"“{esc(p.get('text'))}”"
    if kind == "approval_decided":
        decision = "Aprobó" if p.get("decision") == "aprobar" else "Rechazó"
        return f"{decision} una persona del equipo", f"“{esc(p.get('text'))}”"
    if kind == "case_resolved":
        who = RESOLVED_BY.get(str(p.get("resuelto_por")), "")
        return f"Caso resuelto {who}".strip(), esc(p.get("motivo"))
    if kind == "verification_requested":
        return "Verificación de identidad", "Pidió el email de la compra para enviar un código"
    if kind.startswith("verification"):
        return "Verificación de identidad", VERIFICATION.get(
            str(p.get("result") or kind), esc(json.dumps(p, ensure_ascii=False))
        )
    if kind.endswith("_sent"):
        return "Envió la respuesta", esc(
            CHANNELS.get(str(p.get("channel")), p.get("channel") or "")
        )
    return kind, esc(json.dumps(p, ensure_ascii=False))


ROLES = {"customer": "Cliente", "agent": "Asistente", "human": "Equipo de Acme Store"}


@router.get("/casos/{case_id}", response_class=HTMLResponse)
def case_detail(case_id: uuid.UUID, request: Request, deps: Deps) -> Response:
    session = _session(request)
    if session is None:
        return _to_login()
    if deps.tenant_id is None:
        return _no_tenant()
    tenant_id = deps.tenant_id
    with deps.connection() as conn:
        with tenant_scope(conn, tenant_id):
            case = conn.execute(
                "select c.status, c.escalation, t.name, c.created_at, c.resolved_by from cases c"
                " left join case_types t on t.id = c.case_type_id where c.id = %s",
                (case_id,),
            ).fetchone()
            messages = conn.execute(
                "select role, text, created_at from case_messages where case_id = %s"
                " order by created_at",
                (case_id,),
            ).fetchall()
        if case is None:
            body = '<div class="card empty">Ese caso no existe.</div>'
            return HTMLResponse(page("Caso", body), status_code=404)
        events = audit_log.reconstruct_case(conn, tenant_id, case_id)
        verdict = audit_log.verify_chain(conn, tenant_id, case_id)
        pending = _pending(conn, tenant_id, case_id)
    status, escalation, type_name, created, resolved_by = case
    csrf = auth.csrf_token(session[0])

    chat = (
        "".join(
            f'<div class="msg {esc(role)}"><div class="who">{ROLES.get(role, role)} · '
            f"{local_time(at, '%H:%M')}</div>{esc(text)}</div>"
            for role, text, at in messages
        )
        or '<p class="muted">Sin mensajes.</p>'
    )
    escalated = ""
    if status == "escalated" and escalation:
        summary = escalation if isinstance(escalation, dict) else json.loads(escalation)
        escalated = (
            '<div class="card" style="border-color:var(--bad)"><b>Derivado a una persona</b>'
            f'<div class="muted">Motivo: {esc(summary.get("motivo"))}</div></div>'
        )
    approvals = _approval_cards(pending, csrf, back=f"/panel/casos/{case_id}")

    cost = sum(
        (Decimal(str(e.payload.get("cost_usd") or 0)) for e in events if e.kind == "model_call"),
        Decimal(0),
    )
    tokens = sum(
        int(e.payload.get("input_tokens") or 0) + int(e.payload.get("output_tokens") or 0)
        for e in events
        if e.kind == "model_call"
    )
    calls = sum(1 for e in events if e.kind == "model_call")
    steps = "".join(
        f"<li><b>{esc(title)}</b><span class=small>{detail}</span>"
        f'<div class="muted small">#{e.seq} · {local_time(e.created_at, "%H:%M:%S")}</div></li>'
        for e in events
        for title, detail in [_describe(e.kind, e.payload)]
    )
    chain = (
        '<span class="badge resolved">Cadena de auditoría íntegra</span>'
        if verdict.ok
        else f'<span class="badge escalated">Cadena rota en el evento {verdict.broken_at}: '
        f"{esc(verdict.reason)}</span>"
    )
    body = f"""<p><a href="/panel/casos">← Casos</a></p>
<div class="row"><h1 class="grow">Caso {esc(type_label(type_name))}</h1>
{status_badge(status, resolved_by)}</div>
<p class="lead mono">{case_id} · abierto el {local_time(created, "%d/%m/%Y %H:%M")}</p>
{escalated}{approvals}
<div class="kpis"><div class="kpi"><div class="n">USD {cost:.4f}</div><div class="muted">Costo
del modelo{" (modelo gratuito)" if calls and not cost else ""}</div></div>
<div class="kpi"><div class="n">{calls}</div><div class="muted">llamadas al
modelo · {tokens} tokens</div></div><div class="kpi"><div class="n">{len(events)}</div>
<div class="muted">pasos auditados</div></div></div>
<div class="card"><h2 style="margin-top:0">Conversación</h2><div class="msgs">{chat}</div>
<form method="post" action="/panel/casos/{case_id}/responder" style="margin-top:14px">
<input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="envio" value="{uuid.uuid4().hex}">
<label for="texto">Responder como persona del equipo</label>
<textarea id="texto" name="texto" maxlength="{MAX_REPLY}" required
 placeholder="Hola, soy del equipo de Acme Store…"></textarea>
<p class="muted small">En el chat web el cliente lo ve en unos segundos.</p>
<button class="primary" type="submit">Enviar</button></form></div>
<div class="card"><div class="row"><h2 class="grow" style="margin:0">Auditoría paso a paso</h2>
{chain}</div><p class="muted small">Costo total: USD {cost:.6f}</p>
<ol class="audit">{steps}</ol></div>"""
    return HTMLResponse(page("Caso", body, active="casos"))


@router.post("/casos/{case_id}/responder")
def reply(case_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    session = _session(request)
    if session is None:
        return _to_login()
    if not auth.valid_csrf(session[0], form.get("csrf", "")):
        return _forbidden()
    if deps.tenant_id is None:
        return _no_tenant()
    text = form.get("texto", "").strip()[:MAX_REPLY]
    sending = form.get("envio", "")[:64]  # un token por formulario: el mismo envío, una vez
    if text:
        with deps.connection() as conn:
            with tenant_scope(conn, deps.tenant_id):
                # Bloquear el caso serializa dos envíos simultáneos del mismo formulario.
                exists = conn.execute(
                    "select 1 from cases where id = %s for update", (case_id,)
                ).fetchone()
                repeated = (
                    sending
                    and conn.execute(
                        "select 1 from audit_events where case_id = %s and kind = 'human_reply'"
                        " and payload ->> 'envio' = %s",
                        (case_id, sending),
                    ).fetchone()
                )
            if exists is None:
                body = '<div class="card empty">Ese caso no existe.</div>'
                return HTMLResponse(page("Caso", body), status_code=404)
            if not repeated:
                user_id = _user_id(conn, deps.tenant_id, session[1])
                notify_customer(
                    conn,
                    deps.tenant_id,
                    case_id,
                    text,
                    detail={"by": str(user_id), "envio": sending},
                )
    return RedirectResponse(f"/panel/casos/{case_id}", status_code=303)


# -- reporte ---------------------------------------------------------------------------------
def _pct(value: Any) -> str:
    return f"{round(float(value or 0) * 100)}%"


def _tone(value: float) -> str:
    return "ok" if value >= 0.8 else "warn" if value >= 0.5 else "bad"


@router.get("/reporte", response_class=HTMLResponse)
def report_page(request: Request, deps: Deps) -> Response:
    if _session(request) is None:
        return _to_login()
    report: dict[str, Any] | None = None
    finished = None
    if deps.report_tenant is not None:
        with deps.connection() as conn, tenant_scope(conn, deps.report_tenant):
            row = conn.execute(
                "select report, updated_at from onboarding_runs where status = 'done'"
                " and report is not null order by updated_at desc limit 1"
            ).fetchone()
        if row is not None:
            report, finished = row
    if report is None:
        body = (
            '<h1>Reporte de preparación</h1><div class="card empty">Todavía no hay un '
            "onboarding terminado.</div>"
        )
        return HTMLResponse(page("Reporte", body, active="reporte"))
    body = _report_body(report, finished)
    return HTMLResponse(page("Reporte de preparación", body, active="reporte"))


def _report_body(report: dict[str, Any], finished: Any) -> str:
    types: list[dict[str, Any]] = sorted(
        report.get("tipos") or [], key=lambda t: -int(t.get("volumen") or 0)
    )
    total = int(report.get("conversaciones") or 0)
    top = max((int(t.get("volumen") or 0) for t in types), default=1) or 1
    estimate = float(report.get("resolucion_estimada") or 0)
    adversarial = report.get("adversariales") or {}
    evals = sum(int(t.get("evals") or 0) for t in types)
    rows = []
    gaps: list[str] = []
    for t in types:
        share = float(t.get("porcentaje") or 0)
        volume = int(t.get("volumen") or 0)
        missing = list(t.get("falta") or [])
        if share < 1 and not missing:
            missing = ["Ajustar las instrucciones del tipo con los casos que fallaron"]
        gaps += [f"<b>{esc(type_label(t.get('nombre')))}</b>: {esc(m)}" for m in missing]
        tools = ", ".join(esc(x) for x in t.get("herramientas") or []) or "solo información"
        errors = "".join(
            f"<li>“{esc(e.get('mensaje'))}” — {esc(e.get('motivo'))}</li>"
            for e in t.get("errores") or []
        )
        failed = (
            f'<details class="small"><summary class="muted">Casos que fallaron</summary>'
            f"<ul>{errors}</ul></details>"
            if errors
            else ""
        )
        missing_html = "".join(f"<li>{esc(m)}</li>" for m in missing)
        rows.append(
            f"""<div class="card"><div class="type"><div><b>{esc(type_label(t.get("nombre")))}</b>
<div class="muted small">{esc(t.get("objetivo"))}</div>
<div class="muted small">Herramientas: {tools}</div></div>
<div><div class="row small"><span class="grow muted">Volumen</span><b>{volume}</b>
<span class="muted">({_pct(volume / total if total else 0)})</span></div>
<div class="track"><div class="fill" style="width:{volume / top * 100:.0f}%"></div></div></div>
<div><div class="row small"><span class="grow muted">Resolvería solo</span>
<b>{_pct(share)}</b><span class="muted">({esc(t.get("resueltos"))} de {esc(t.get("evals"))})
</span></div>
<div class="track"><div class="fill {_tone(share)}" style="width:{share * 100:.0f}%"></div></div>
</div></div>{f'<ul class="missing">{missing_html}</ul>' if missing_html else ""}{failed}</div>"""
        )
    adversarial_fails = "".join(
        f"<li>“{esc(f.get('mensaje'))}” — {esc(f.get('motivo'))}</li>"
        for f in adversarial.get("fallas") or []
    )
    gaps_html = "".join(f"<li>{g}</li>" for g in gaps) or "<li>Nada pendiente.</li>"
    when = f" · {local_time(finished, '%d/%m/%Y')}" if finished else ""
    return f"""<h1>Reporte de preparación</h1>
<p class="lead">Qué parte de la atención de Acme Store resolvería el asistente por su cuenta,
medido con su propio historial{when}.</p>
<div class="kpis"><div class="kpi big"><div class="n">{_pct(estimate)}</div><div class="muted">del
volumen lo resolvería solo</div></div><div class="kpi"><div class="n">{total}</div>
<div class="muted">conversaciones analizadas</div></div><div class="kpi"><div class="n">
{len(types)}</div><div class="muted">tipos de caso detectados</div></div><div class="kpi">
<div class="n">{esc(adversarial.get("superados", 0))} de {esc(adversarial.get("total", 0))}</div>
<div class="muted">ataques y pedidos indebidos frenados</div></div></div>
<h2>Tipos de caso</h2>{"".join(rows)}
<h2>Qué falta para resolver más</h2><div class="card"><ul>{gaps_html}</ul>
{
        f'<p class="muted small">Ataques no frenados:</p><ul class="small">{adversarial_fails}</ul>'
        if adversarial_fails
        else ""
    }</div>
<p class="muted small">Cómo se mide: el historial se anonimiza y se agrupa en tipos de caso; por
cada tipo se generan casos de prueba a partir de conversaciones reales ({evals} en total) y el
asistente los resuelve contra una copia de prueba del sistema, sin tocar datos reales. El
porcentaje pondera cada tipo por su volumen. Los reembolsos cuentan como resueltos cuando el
asistente prepara el pedido de aprobación: la decisión siempre es de una persona.</p>"""
