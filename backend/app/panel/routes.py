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
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any
from urllib.parse import parse_qs

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import i18n
from app.audit import log as audit_log
from app.cases import feedback, refund_confirmation, supervision
from app.cases.archive import archive_cases
from app.cases.closing import close_inactive
from app.companies.connection import ClientFactory, http_client
from app.db.connection import connect, tenant_scope
from app.llm.embedder import Embedder
from app.mailer import Mailer, mailer_from_env
from app.panel import auth, english
from app.panel.html import (
    ADMIN_ONLY,
    PENDING,
    RESOLVED_BY,
    esc,
    local_time,
    page,
    status_badge,
    toast,
)
from app.panel.labels import action_label, money, type_label
from app.panel.notify import notify_customer
from app.panel.privacy import mask_codes, summary
from app.policy.approvals import ApprovalError, ApprovalOutcome, decide
from app.policy.guardian import Guardian

router = APIRouter(prefix="/panel", include_in_schema=False)

REPORT_TENANT = "Acme Store (onboarding)"
MAX_REPLY = 2000


def customer_texts(case_column: str) -> str:
    """Los primeros mensajes del cliente del caso, en orden: de ahí sale el resumen."""
    return (
        "array(select m.text from case_messages m where m.case_id = "
        f"{case_column} and m.role = 'customer' order by m.created_at limit 10)"
    )


def _no_embedder(conn: psycopg.Connection) -> Embedder:
    raise RuntimeError("Falta configurar los embeddings")


@dataclass
class PanelDeps:
    connection: Callable[[], AbstractContextManager[psycopg.Connection]]
    tenant_id: uuid.UUID | None  # la empresa de demo (Acme): a la que entra el operador
    # El guardián de la empresa de la sesión: sus herramientas llegan a su propio sistema.
    guardian: Callable[[psycopg.Connection, uuid.UUID], Guardian]
    report_tenant: uuid.UUID | None = None  # reporte de Acme del hito 4 (otro tenant)
    # "Reiniciar demo" también devuelve los pedidos de la API simulada a su estado inicial.
    reset_sandbox: Callable[[], None] = lambda: None
    # Onboarding desde el panel: embeddings para indexar documentos, aviso al worker y cliente
    # HTTP para probar la conexión con el sistema de la empresa.
    embedder: Callable[[psycopg.Connection], Embedder] = _no_embedder
    kick_worker: Callable[[], None] = lambda: None
    client_factory: ClientFactory = http_client
    # El email de confirmación de un reembolso aprobado (decisión 046).
    mailer: Callable[[], Mailer] = mailer_from_env
    # La entrega de un borrador aprobado del modo supervisado por su canal (decisión 072).
    deliver: supervision.Deliver = supervision.deliver


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
    from app.companies.agent import build_guardian
    from app.companies.worker import kick
    from app.config import load_dotenv
    from app.demo.acme import find_demo_tenant, shared_acme
    from app.demo.northwind import shared_northwind

    def reset_sandboxes() -> None:  # las APIs simuladas de las empresas de demo
        shared_acme.cache_clear()
        shared_northwind.cache_clear()

    from app.llm.client import PostgresUsageRecorder
    from app.llm.factory import embedder_from_env

    load_dotenv()
    with _transaction() as conn:
        tenant_id = find_demo_tenant(conn)
        row = conn.execute("select id from tenants where name = %s", (REPORT_TENANT,)).fetchone()
    return PanelDeps(
        connection=_transaction,
        tenant_id=tenant_id,
        # Acme: la misma API simulada que usa el agente del chat (lo aprobado se ve en el
        # pedido). Las demás empresas: su sistema, o el modo simulado hasta que lo activen.
        guardian=build_guardian,
        report_tenant=row[0] if row else None,
        reset_sandbox=reset_sandboxes,
        embedder=lambda conn: embedder_from_env(PostgresUsageRecorder(conn)),
        kick_worker=kick,
    )


def panel_deps() -> PanelDeps:
    return _production_deps()


Deps = Annotated[PanelDeps, Depends(panel_deps)]


async def form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


Form = Annotated[dict[str, str], Depends(form_data)]


DEMO_COMPANY = "Acme Store"


def _session(request: Request, deps: PanelDeps) -> auth.Session | None:
    """La sesión del panel con su empresa, o None. La empresa sale siempre de acá (de la cookie
    firmada), nunca de la URL ni de un formulario: un usuario solo ve y toca la suya."""
    session = auth.read_claims(request.cookies.get(auth.COOKIE))
    if session is None:
        return None
    if session.admin:  # operador de la plataforma: entra a la empresa de demo
        if not auth.configured() or session.email != auth.operator_email():
            return None
        return replace(session, tenant_id=deps.tenant_id, company=DEMO_COMPANY)
    return session if session.tenant_id is not None else None


def _to_login() -> RedirectResponse:
    return RedirectResponse("/panel/login", status_code=303)


def _forbidden(message: str = "El formulario venció. Volvé a cargar la página.") -> HTMLResponse:
    body = f'<div class="card empty">{esc(message)}</div>'
    return HTMLResponse(page("Sin permiso", body), status_code=403)


def _not_found(what: str, session: auth.Session) -> HTMLResponse:
    """La misma respuesta si no existe o si es de otra empresa: no se revela cuál de las dos."""
    body = f'<div class="card empty">{esc(what)} no existe.</div>'
    return HTMLResponse(page("No existe", body, session=session), status_code=404)


def _no_tenant() -> HTMLResponse:
    body = '<div class="card empty">La empresa de la demo todavía no está cargada.</div>'
    return HTMLResponse(page("Panel", body), status_code=503)


@dataclass(frozen=True)
class Ctx:
    """Lo que necesita una ruta ya autorizada: la sesión, su empresa y el token anti-CSRF."""

    session: auth.Session
    tenant_id: uuid.UUID
    csrf: str
    demo: bool = False  # una empresa de demo (Acme o Northwind): se puede reiniciar

    @property
    def can_reset(self) -> bool:
        return self.session.admin or self.demo


def enter(request: Request, deps: PanelDeps, form: dict[str, str] | None = None) -> Ctx | Response:
    """Puerta de entrada de cada ruta del panel. Sin sesión, va al login. Con `form` (un POST
    que cambia algo), exige además el token anti-CSRF. Devuelve la empresa de la sesión."""
    english.set_language("es")
    session = _session(request, deps)
    if session is None:
        return _to_login()
    if form is not None and not auth.valid_csrf(session.token, form.get("csrf", "")):
        return _forbidden()
    if session.tenant_id is None:
        return _no_tenant()
    # Una persona del equipo no entra a la configuración (decisión 058).
    section = request.url.path.removeprefix("/panel/").split("/", 1)[0]
    if session.role == "agent" and section in ADMIN_ONLY:
        return _forbidden("Esta sección es solo para los admins de la empresa.")
    active, language, demo, pending = _company(deps, session.tenant_id)
    if not session.admin and not active:
        return _to_login()  # la empresa fue borrada: su sesión deja de servir
    # El panel habla el idioma de la empresa (decisión 037): vale para todo este pedido.
    english.set_language(language)
    PENDING.set(pending)  # el contador del menú
    return Ctx(session, session.tenant_id, auth.csrf_token(session.token), demo=demo)


def _company(deps: PanelDeps, tenant_id: uuid.UUID) -> tuple[bool, str, bool, int]:
    """(¿la empresa existe y está activa?, su idioma, ¿es una empresa de demo?, aprobaciones
    pendientes)."""
    with deps.connection() as conn, tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select status = 'active', settings ->> 'language', settings ->> 'demo',"
            " (select count(*) from approvals a join cases c on c.id = a.case_id"
            "  and c.archived_at is null where a.status = 'pending')"
            " from tenants where id = %s",
            (tenant_id,),
        ).fetchone()
    if row is None:
        return False, "es", False, 0
    return bool(row[0]), i18n.normalize(row[1]), bool(row[2]), int(row[3])


def _money(value: Any) -> str:
    return esc(money(value))


# -- login -----------------------------------------------------------------------------------
def _login_page(error: str = "") -> str:
    note = (
        ""
        if auth.configured()
        else '<p class="muted small">Falta configurar PANEL_EMAIL y PANEL_PASSWORD.</p>'
    )
    alert = f'<p style="color:var(--bad)">{esc(error)}</p>' if error else ""
    # Antes de entrar no se sabe la empresa: el idioma se elige acá (decisión 037).
    other = (
        '<a href="/panel/login">Español</a>'
        if english.english()
        else '<a href="/panel/login?lang=en">English</a>'
    )
    body = f"""<div class="card" style="max-width:400px;margin:40px auto">
<div class="row"><h1 class="grow">Ingresar al panel</h1><span class="small">{other}</span></div>
<p class="lead">Panel del equipo de atención.</p>{alert}{note}
<form method="post" action="/panel/login">
<input type="hidden" name="lang" value="{"en" if english.english() else "es"}">
<label for="email">Email</label><input id="email" name="email" type="email" required
 autocomplete="username">
<label for="password">Clave</label><input id="password" name="password" type="password" required
 autocomplete="current-password">
<p><button class="primary" type="submit" style="width:100%">Ingresar</button></p></form>
<p class="muted small">¿Tu empresa no tiene cuenta? <a href="/registro">Creala acá</a>.</p></div>"""
    return page("Ingresar", body, logged_in=False)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request, deps: Deps) -> Response:
    return RedirectResponse("/panel/casos" if _session(request, deps) else "/panel/login", 303)


@router.get("/login", response_class=HTMLResponse)
def login_form(lang: str = "") -> HTMLResponse:
    english.set_language(lang)
    return HTMLResponse(_login_page())


@router.post("/login")
def login(request: Request, form: Form, deps: Deps) -> Response:
    english.set_language(form.get("lang", ""))
    email, password = form.get("email", "").strip().lower(), form.get("password", "")
    if auth.too_many_failures(email):
        message = "Demasiados intentos. Probá de nuevo en unos minutos."
        return HTMLResponse(_login_page(message), status_code=429)
    if auth.configured() and auth.check_credentials(email, password):
        token = auth.new_session(email)  # operador de la plataforma: entra a la demo
    else:
        with deps.connection() as conn:
            user = auth.company_login(conn, email, password)
        if user is None:
            auth.record_failure(email)
            return HTMLResponse(_login_page("Email o clave incorrectos."), status_code=401)
        token = auth.new_session(
            user.email, tenant_id=user.tenant_id, company=user.company, admin=False, role=user.role
        )
    auth.clear_failures(email)
    response = RedirectResponse("/panel/casos", status_code=303)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        auth.COOKIE,
        token,
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


def _approval_cards(rows: list[tuple[Any, ...]], csrf: str, *, back: str) -> str:
    cards = []
    for approval_id, tool, params, case_id, created, customer_messages in rows:
        first_message = summary(customer_messages or [])
        cards.append(
            f"""<div class="card attention"><div class="row"><div class="grow">
<span class="badge waiting_approval">Pendiente</span> <b>{esc(action_label(tool, params))}</b>
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
            f" {customer_texts('a.case_id')}"
            " from approvals a join tools t on t.id = a.tool_id"
            " join cases c on c.id = a.case_id and c.archived_at is null"
            " where a.status = 'pending' and (%s::uuid is null or a.case_id = %s)"
            " order by a.created_at",
            (case_id, case_id),
        ).fetchall()


@router.get("/aprobaciones", response_class=HTMLResponse)
def approvals_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        pending = _pending(conn, ctx.tenant_id)
        with tenant_scope(conn, ctx.tenant_id):
            decided = conn.execute(
                "select t.name, a.params, a.status, a.decided_at, a.case_id, u.email"
                " from approvals a join tools t on t.id = a.tool_id"
                " join cases c on c.id = a.case_id and c.archived_at is null"
                " left join users u on u.id = a.decided_by"
                " where a.status <> 'pending' order by a.decided_at desc nulls last limit 10"
            ).fetchall()
    cards = _approval_cards(pending, ctx.csrf, back="/panel/aprobaciones") or (
        '<div class="card empty">No hay pedidos esperando aprobación.</div>'
    )
    history = "".join(
        f"""<div class="card row"><span class="badge {
            "resolved" if status == "approved" else "escalated"
        }">{"Aprobado" if status == "approved" else "Rechazado"}</span>
<span class="grow">{esc(action_label(tool, params))}</span>
<span class="muted small">{esc(email or "")} · {local_time(decided)}</span>
<a href="/panel/casos/{case_id}">ver caso</a></div>"""
        for tool, params, status, decided, case_id, email in decided
        if decided is not None
    )
    body = f"""{toast(request.query_params.get("aviso"))}
<h1>Aprobaciones</h1><p class="lead">Las acciones de riesgo alto (como un reembolso)
no se ejecutan hasta que una persona las aprueba. Al aprobar, se ejecuta por el guardián y el
cliente recibe el aviso en el chat.</p>{cards}
{f"<h2>Decididas</h2>{history}" if history else ""}"""
    return HTMLResponse(page("Aprobaciones", body, active="aprobaciones", session=ctx.session))


def _approval_message(
    tool: str, params: dict[str, Any], outcome: ApprovalOutcome, language: str = "es"
) -> str:
    """El aviso al cliente, en el idioma de la empresa (decisión 036)."""
    return i18n.approval_message(language, tool, params, outcome.status)


@router.post("/aprobaciones/{approval_id}")
def decide_approval(approval_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    approve = form.get("decision") == "aprobar"
    back = form.get("volver", "")
    with deps.connection() as conn:
        with tenant_scope(conn, ctx.tenant_id):
            row = conn.execute(
                "select a.case_id, t.name, a.params from approvals a"
                " join tools t on t.id = a.tool_id where a.id = %s",
                (approval_id,),
            ).fetchone()
        if row is None:
            return _not_found("Esa aprobación", ctx.session)
        case_id, tool, params = row
        user_id = _user_id(conn, ctx.tenant_id, ctx.session.email)
        try:
            outcome = decide(
                conn,
                deps.guardian(conn, ctx.tenant_id),
                ctx.tenant_id,
                approval_id,
                approve=approve,
                decided_by=user_id,
            )
        except ApprovalError:
            # Ya decidida (doble clic o reenvío): no se ejecuta ni se avisa de nuevo.
            return RedirectResponse("/panel/aprobaciones", status_code=303)
        notice = "rechazado" if not approve else "aprobado"
        if approve and outcome.status != "executed":
            notice = "fallo"
        notify_customer(
            conn,
            ctx.tenant_id,
            case_id,
            _approval_message(tool, params, outcome, i18n.tenant_language(conn, ctx.tenant_id)),
            kind="approval_decided",
            detail={
                "approval_id": str(approval_id),
                "decision": "aprobar" if approve else "rechazar",
                "status": outcome.status,
                "decided_by": str(user_id),
            },
        )
        if outcome.status == "executed" and tool == refund_confirmation.REFUND_TOOL:
            mailed = refund_confirmation.send(
                conn, deps.mailer(), ctx.tenant_id, approval_id, outcome.result
            )
            notice = {
                "sent": "reembolso-email",
                "already_sent": "reembolso-email",
                "failed": "reembolso-sin-email",
            }.get(mailed, "reembolso")
    target = back if back.startswith("/panel/") else "/panel/aprobaciones"
    target += ("&" if "?" in target else "?") + f"aviso={notice}"
    return RedirectResponse(target, status_code=303)


# -- bandeja ---------------------------------------------------------------------------------
CHANNELS = {"web": "Chat web", "twilio_whatsapp": "WhatsApp", "whatsapp": "WhatsApp"}
# Tipo del caso; si no es un tipo de la empresa (charla, consulta general, pedido de una
# persona), la última clasificación registrada en la auditoría.
CASE_LABEL = (
    "coalesce(t.name, (select e.payload ->> 'case_type' from audit_events e"
    " where e.case_id = c.id and e.kind = 'classified' order by e.seq desc limit 1))"
)


@router.get("/casos", response_class=HTMLResponse)
def inbox(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        # Regla de cierre: los que llevan 24 h sin respuesta del cliente quedan resueltos.
        close_inactive(conn, ctx.tenant_id)
        rows = _inbox_rows(conn, ctx.tenant_id)
        drafts = _draft_cards(conn, ctx.tenant_id, ctx.csrf, back="/panel/casos")
        waiting: dict[Any, tuple[Any, ...]] = {}  # caso -> su primera aprobación pendiente
        for approval in _pending(conn, ctx.tenant_id):
            waiting.setdefault(approval[3], approval)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[1]] = counts.get(row[1], 0) + 1
    kpis = "".join(
        f'<div class="kpi"><div class="n">{counts.get(key, 0)}</div>'
        f'<div class="muted">{label}</div></div>'
        for key, label in (
            ("escalated", "Derivados a una persona"),
            ("waiting_approval", "Esperando aprobación"),
            ("open", "Abiertos"),
            ("resolved", "Resueltos"),
        )
    )

    def item(
        case_id: Any,
        status: str,
        updated: Any,
        type_name: Any,
        preview: Any,
        total: int,
        channel: Any,
        verified: bool,
        resolved_by: Any,
        reopened: bool,
    ) -> str:
        head = f"""<div class="row" style="gap:8px">
{status_badge(status, resolved_by, reopened)}
<span class="muted small">{esc(type_label(type_name))}</span>
<span class="muted small">· {esc(CHANNELS.get(channel or "", "Canal"))}
{" · verificado" if verified else ""}</span></div>"""
        text = esc(summary(preview or []) or "(sin texto)")
        approval = waiting.get(case_id)
        if approval is not None:
            # Espera una aprobación: resaltada, con lo que hay que decidir y los botones a la
            # vista (demo en inglés, cambio D).
            approval_id, tool, params = approval[:3]
            return f"""<div class="card row attention"><a class="grow" href="/panel/casos/{case_id}"
style="text-decoration:none;color:inherit;min-width:0">{head}
<div style="margin-top:6px"><b>{esc(action_label(tool, params))}</b></div>
<div class="ellipsis muted small" style="margin-top:2px">{text}</div></a>
<form method="post" action="/panel/aprobaciones/{approval_id}" class="row">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<input type="hidden" name="volver" value="/panel/casos">
<button class="danger" name="decision" value="rechazar">Rechazar</button>
<button class="primary" name="decision" value="aprobar">Aprobar</button></form></div>"""
        return f"""<a class="card row" href="/panel/casos/{case_id}" style="text-decoration:none;
color:inherit"><div class="grow">{head}
<div class="ellipsis" style="margin-top:6px">{text}
</div></div>
<div class="muted small" style="text-align:right">{local_time(updated)}<br>{total} mensajes
</div></a>"""

    items = "".join(item(*row) for row in rows) or (
        '<div class="card empty">Todavía no hay conversaciones.</div>'
    )
    reset = '<a class="btn" href="/panel/reiniciar">Reiniciar demo</a>' if ctx.can_reset else ""
    body = f"""{toast(request.query_params.get("aviso"))}
<div class="row"><h1 class="grow">Casos</h1>{reset}</div>
<p class="lead">Conversaciones con su estado: primero las derivadas
a una persona, después las que esperan una aprobación. Un caso se resuelve cuando el cliente
confirma o pasan 24 h sin respuesta.</p><div class="kpis">{kpis}</div>
{drafts}{items}"""
    return HTMLResponse(page("Casos", body, active="casos", session=ctx.session))


@router.get("/reiniciar", response_class=HTMLResponse)
def reset_form(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.can_reset:  # solo una empresa de demo se reinicia
        return _forbidden("Solo el operador de la demo puede reiniciarla.")
    api = "de Acme" if ctx.session.admin else "simulada"
    body = f"""<div class="card" style="max-width:560px;margin:24px auto">
<h1>Reiniciar demo</h1>
<p>Deja el panel limpio para empezar una demo nueva:</p>
<ul><li>Las conversaciones de prueba se archivan: salen del panel y del chat.</li>
<li>Las aprobaciones pendientes se rechazan, así no se ejecutan después.</li>
<li>Los pedidos de la API {api} vuelven a su estado inicial.</li></ul>
<p class="muted small">La auditoría de cada caso queda guardada e íntegra: es de solo
inserción y no se borra.</p>
<form method="post" action="/panel/reiniciar" class="row">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<a class="btn" href="/panel/casos">Cancelar</a>
<button class="primary" type="submit">Reiniciar demo</button></form></div>"""
    return HTMLResponse(page("Reiniciar demo", body, active="casos", session=ctx.session))


@router.post("/reiniciar")
def reset_demo(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if not ctx.can_reset:
        return _forbidden("Solo el operador de la demo puede reiniciarla.")
    with deps.connection() as conn:
        archive_cases(conn, ctx.tenant_id, _user_id(conn, ctx.tenant_id, ctx.session.email))
    deps.reset_sandbox()
    return RedirectResponse("/panel/casos", status_code=303)


def _inbox_rows(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[Any, ...]]:
    with tenant_scope(conn, tenant_id):
        return conn.execute(
            f"select c.id, c.status, c.updated_at, {CASE_LABEL}, {customer_texts('c.id')},"
            " (select count(*) from case_messages m where m.case_id = c.id),"
            " (select e.payload ->> 'channel' from audit_events e where e.case_id = c.id"
            "  and e.payload ? 'channel' limit 1),"
            " coalesce(k.verified_until > now(), false), c.resolved_by, c.reopened"
            " from cases c join contacts k on k.id = c.contact_id"
            " left join case_types t on t.id = c.case_type_id"
            " where c.archived_at is null"
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
    "verification_code_failed": "No se pudo enviar el código por email",
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


def _seconds(milliseconds: Any) -> str:
    """`1234` -> `1,2 s`."""
    return f"{int(milliseconds or 0) / 1000:.1f} s".replace(".", ",")


def _describe(kind: str, p: dict[str, Any]) -> tuple[str, str]:
    """(título, detalle en HTML) de un evento de auditoría."""
    if kind == "message_received":
        verified = "verificado" if p.get("verified") else "sin verificar"
        return (
            "Mensaje del cliente",
            f"“{esc(mask_codes(str(p.get('text'))))}” <span class=muted>· {verified}</span>",
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
    if kind == "format_rejected":
        return "Descartó una respuesta que no se podía mostrar", esc(p.get("reason"))
    if kind == "tone_rejected":  # decisión 040
        return "Pidió empatía antes de responder", esc(p.get("reason"))
    if kind == "second_topic":
        return "Atendió un segundo tema", esc(type_label(p.get("case_type")))
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
    if kind == refund_confirmation.SENT:  # decisión 046
        return "Envió el email de confirmación del reembolso", (
            f"Referencia <span class=mono>{esc(p.get('reference'))}</span>"
        )
    if kind == refund_confirmation.FAILED:
        return "No se pudo enviar el email de confirmación del reembolso", esc(p.get("motivo"))
    if kind == "case_resolved":
        who = RESOLVED_BY.get(str(p.get("resuelto_por")), "")
        return f"Caso resuelto {who}".strip(), esc(p.get("motivo"))
    if kind == "csat_answered":
        said = "se resolvió" if p.get("resuelto") else "no se resolvió"
        extra = " (dejó un comentario)" if p.get("con_comentario") else ""
        return "Satisfacción", f"El cliente dijo que su consulta {said}{extra}"
    if kind == "case_reopened":
        return "Caso reabierto", esc(p.get("motivo"))
    if kind == "turn_timing":
        parts = " · ".join(
            f"{label} {_seconds(p.get(key))}"
            for key, label in (
                ("modelo_ms", "modelo"),
                ("base_ms", "base de datos"),
                ("busqueda_ms", "búsqueda"),
                ("herramientas_ms", "herramientas"),
            )
        )
        return "Tiempo de la respuesta", f"{_seconds(p.get('total_ms'))} en total · {parts}"
    if kind == "approval_message_fixed":
        return "Dejó el pedido en revisión con el mensaje fijo", esc(p.get("reason"))
    if kind == "verification_requested":
        return "Verificación de identidad", "Pidió el email de la compra para enviar un código"
    if kind.startswith("verification"):
        return "Verificación de identidad", VERIFICATION.get(
            str(p.get("result") or kind), esc(json.dumps(p, ensure_ascii=False))
        )
    if kind == "draft_held":  # decisión 072
        return "Dejó la respuesta como borrador (modo supervisado)", f"“{esc(p.get('text'))}”"
    if kind == "draft_sent":
        title = (
            "Una persona editó y envió el borrador"
            if p.get("edited")
            else ("Una persona aprobó y envió el borrador")
        )
        return title, f"“{esc(p.get('text'))}”"
    if kind == "draft_rejected":
        return "Una persona rechazó el borrador", "No se envió al cliente"
    if kind == "draft_delivered":
        return "Envió la respuesta aprobada", esc(
            CHANNELS.get(str(p.get("channel") or "whatsapp"), p.get("channel") or "")
        )
    if kind.endswith("_sent"):
        return "Envió la respuesta", esc(
            CHANNELS.get(str(p.get("channel")), p.get("channel") or "")
        )
    return kind, esc(json.dumps(p, ensure_ascii=False))


ROLES = {"customer": "Cliente", "agent": "Asistente"}


@router.get("/casos/{case_id}", response_class=HTMLResponse)
def case_detail(case_id: uuid.UUID, request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    tenant_id, company = ctx.tenant_id, ctx.session.company
    with deps.connection() as conn:
        with tenant_scope(conn, tenant_id):
            case = conn.execute(
                f"select c.status, c.escalation, {CASE_LABEL}, c.created_at, c.resolved_by,"
                " c.reopened"
                " from cases c left join case_types t on t.id = c.case_type_id"
                " where c.id = %s and c.archived_at is null",
                (case_id,),
            ).fetchone()
            messages = conn.execute(
                "select role, text, created_at, id from case_messages where case_id = %s"
                " order by created_at",
                (case_id,),
            ).fetchall()
        if case is None:
            return _not_found("Ese caso", ctx.session)
        events = audit_log.reconstruct_case(conn, tenant_id, case_id)
        verdict = audit_log.verify_chain(conn, tenant_id, case_id)
        pending = _pending(conn, tenant_id, case_id)
        reviewed = feedback.marks(conn, tenant_id, case_id)
        drafts = _draft_cards(
            conn, tenant_id, ctx.csrf, back=f"/panel/casos/{case_id}", case_id=case_id
        )
    status, escalation, type_name, created, resolved_by, reopened = case
    csrf = ctx.csrf
    roles = {**ROLES, "human": f"Equipo de {company}"}

    # Cada respuesta del asistente se puede marcar como correcta o incorrecta (decisión 073).
    from app.panel.review_routes import review_form

    chat = (
        "".join(
            f'<div class="msg {esc(role)}"><div class="who">{esc(roles.get(role, role))} · '
            f"{local_time(at, '%H:%M')}</div>{esc(mask_codes(text))}</div>"
            + (review_form(ctx, case_id, mid, reviewed.get(mid)) if role == "agent" else "")
            for role, text, at, mid in messages
        )
        or '<p class="muted">Sin mensajes.</p>'
    )
    escalated = ""
    if status == "escalated" and escalation:
        handoff = escalation if isinstance(escalation, dict) else json.loads(escalation)
        escalated = (
            '<div class="card" style="border-color:var(--bad)"><b>Derivado a una persona</b>'
            f'<div class="muted">Motivo: {esc(handoff.get("motivo"))}</div></div>'
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
    body = f"""{toast(request.query_params.get("aviso"))}<p><a href="/panel/casos">← Casos</a></p>
<div class="row"><h1 class="grow">Caso {esc(type_label(type_name))}</h1>
{status_badge(status, resolved_by, reopened)}</div>
<p class="lead mono">{case_id} · abierto el {local_time(created, "%d/%m/%Y %H:%M")}</p>
{escalated}{approvals}{drafts}
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
 placeholder="Hola, soy del equipo de {esc(company)}…"></textarea>
<p class="muted small">En el chat web el cliente lo ve en unos segundos.</p>
<button class="primary" type="submit">Enviar</button></form></div>
<div class="card"><div class="row"><h2 class="grow" style="margin:0">Auditoría paso a paso</h2>
{chain}</div><p class="muted small">Costo total: USD {cost:.6f}</p>
<ol class="audit">{steps}</ol></div>"""
    return HTMLResponse(page("Caso", body, active="casos", session=ctx.session))


@router.post("/casos/{case_id}/responder")
def reply(case_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    text = form.get("texto", "").strip()[:MAX_REPLY]
    sending = form.get("envio", "")[:64]  # un token por formulario: el mismo envío, una vez
    if text:
        with deps.connection() as conn:
            with tenant_scope(conn, ctx.tenant_id):
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
                return _not_found("Ese caso", ctx.session)
            if not repeated:
                user_id = _user_id(conn, ctx.tenant_id, ctx.session.email)
                notify_customer(
                    conn,
                    ctx.tenant_id,
                    case_id,
                    text,
                    detail={"by": str(user_id), "envio": sending},
                )
    return RedirectResponse(f"/panel/casos/{case_id}", status_code=303)


# -- borradores del modo supervisado (decisión 072) -----------------------------------------
def _draft_cards(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    csrf: str,
    *,
    back: str,
    case_id: uuid.UUID | None = None,
) -> str:
    """Las respuestas del asistente que esperan a una persona: texto editable, Enviar y
    Rechazar. En la bandeja (todas) y en el detalle de un caso (las suyas)."""
    drafts = supervision.pending(conn, tenant_id, case_id)
    if not drafts:
        return ""
    cards = "".join(
        f"""<div class="card attention"><div class="row" style="gap:8px">
<span class="badge waiting_approval">Borrador</span>
<span class="muted small"><span>{esc(CHANNELS.get(d.channel, "Canal"))}</span> ·
<span>{local_time(d.created_at)}</span></span>
{"" if case_id else f'<a class="small" href="/panel/casos/{d.case_id}">ver caso</a>'}</div>
<form method="post" action="/panel/borradores/{d.id}" style="margin-top:8px">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="volver" value="{back}">
<label for="borrador-{d.id}">Respuesta del asistente (podés editarla antes de enviarla)</label>
<textarea id="borrador-{d.id}" name="texto"
 maxlength="{MAX_REPLY}" required>{esc(d.text)}</textarea>
<div class="row" style="justify-content:flex-end">
<button class="danger" name="decision" value="rechazar">Rechazar</button>
<button class="primary" name="decision" value="enviar">Enviar</button></div></form></div>"""
        for d in drafts
    )
    return f"""<h2>Borradores por revisar</h2><p class="muted small">Modo supervisado: el cliente
ve “escribiendo…” hasta que una persona envía o rechaza la respuesta.</p>{cards}"""


@router.post("/borradores/{draft_id}")
def decide_draft(draft_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    back = form.get("volver", "")
    target = back if back.startswith("/panel/") else "/panel/casos"
    with deps.connection() as conn:
        user_id = _user_id(conn, ctx.tenant_id, ctx.session.email)
        try:
            if form.get("decision") == "enviar":
                supervision.send(
                    conn,
                    ctx.tenant_id,
                    draft_id,
                    form.get("texto", "")[:MAX_REPLY],
                    decided_by=user_id,
                    deliver=deps.deliver,
                )
                notice = "borrador-enviado"
            else:
                supervision.reject(conn, ctx.tenant_id, draft_id, decided_by=user_id)
                notice = "borrador-rechazado"
        except supervision.DraftError:
            # Ya decidido (doble clic o reenvío): no se envía de nuevo.
            return RedirectResponse(target, status_code=303)
    target += ("&" if "?" in target else "?") + f"aviso={notice}"
    return RedirectResponse(target, status_code=303)


# -- reporte ---------------------------------------------------------------------------------
def _pct(value: Any) -> str:
    return f"{round(float(value or 0) * 100)}%"


def _tone(value: float) -> str:
    return "ok" if value >= 0.8 else "warn" if value >= 0.5 else "bad"


@router.get("/reporte", response_class=HTMLResponse)
def report_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    # El reporte de la propia empresa. Acme, además, conserva el de su corrida del hito 4, que
    # quedó en otro tenant: se usa solo si la empresa de demo no tiene una corrida propia.
    sources = [ctx.tenant_id]
    if ctx.session.admin and deps.report_tenant is not None:
        sources.append(deps.report_tenant)
    report: dict[str, Any] | None = None
    finished = None
    from app.companies import go_live
    from app.panel.live_routes import readiness_html

    with deps.connection() as conn:
        # Probar y salir en vivo (decisión 056): arriba de todo.
        live = readiness_html(ctx, go_live.readiness(conn, ctx.tenant_id))
        for source in sources:
            with tenant_scope(conn, source):
                row = conn.execute(
                    "select report, updated_at from onboarding_runs where status = 'done'"
                    " and report is not null order by updated_at desc limit 1"
                ).fetchone()
            if row is not None:
                report, finished = row
                break
    if report is None:
        body = (
            f'<h1>Reporte de preparación</h1>{live}<div class="card empty">Todavía no hay un '
            'onboarding terminado. Empezá en <a href="/panel/onboarding">Onboarding</a>.</div>'
        )
        return HTMLResponse(page("Reporte", body, active="reporte", session=ctx.session))
    body = _report_body(report, finished, ctx.session.company).replace("</h1>", f"</h1>{live}", 1)
    return HTMLResponse(page("Reporte de preparación", body, active="reporte", session=ctx.session))


def _report_body(report: dict[str, Any], finished: Any, company: str) -> str:
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
<p class="lead">Qué parte de la atención de {esc(company)} resolvería el asistente por su cuenta,
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
