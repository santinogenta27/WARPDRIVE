"""Home (Hito 13, tarea 3; decisión 085): lo primero que ve el cliente.

- Primeros pasos hasta salir en vivo, empezando por el conocimiento (decisión 074).
- "Needs your attention": aprobaciones, derivaciones y borradores pendientes, cada uno con un
  clic para resolverlo. Aprobar y rechazar vuelven a Home.
- Los números de los últimos 7 días: conversaciones, resueltas por el agente, derivadas, ventas
  atribuidas y costo del modelo.
- El estado del agente (en vivo, en pausa o configurándose) y el de cada canal.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.cases import supervision
from app.channels.whatsapp import selfservice as whatsapp
from app.companies import connection as system
from app.companies import first_steps, go_live
from app.db.connection import tenant_scope
from app.panel import english
from app.panel.html import esc, local_time, page, toast
from app.panel.labels import action_label, money
from app.panel.privacy import summary
from app.panel.routes import Ctx, Deps, _pending, enter
from app.sales import attribution

router = APIRouter(prefix="/panel", include_in_schema=False)
WEEK = 7
MAX_ITEMS = 8  # por tipo de pendiente: el resto, en Inbox


@dataclass
class Week:
    conversations: int = 0
    resolved_by_agent: int = 0
    handed_off: int = 0
    sales: list[attribution.SalesTotal] = field(default_factory=list)
    cost: Decimal = Decimal(0)


@dataclass
class Channels:
    web_installed: bool
    whatsapp: str  # "active", "inactive" o "" (sin conectar)
    store: str  # el modo de `connection.mode_for`


def week(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Week:
    since = datetime.now(UTC) - timedelta(days=WEEK)
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select count(*),"
            " count(*) filter (where status = 'resolved' and resolved_by = 'agent'),"
            " count(*) filter (where exists (select 1 from audit_events e"
            "  where e.case_id = c.id and e.kind = 'escalated'))"
            " from cases c where archived_at is null and created_at > %s",
            (since,),
        ).fetchone()
        cost = conn.execute(
            "select coalesce(sum(cost_usd), 0) from usage where created_at > %s", (since,)
        ).fetchone()
    found = Week(int(row[0]), int(row[1]), int(row[2])) if row else Week()
    found.sales = attribution.totals(conn, tenant_id, since)
    found.cost = Decimal(cost[0]) if cost else Decimal(0)
    return found


def handoffs(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Casos derivados a una persona que todavía esperan al equipo, del más viejo al más nuevo."""
    with tenant_scope(conn, tenant_id):
        return conn.execute(
            "select c.id, c.updated_at, array(select m.text from case_messages m"
            "  where m.case_id = c.id and m.role = 'customer' order by m.created_at limit 10)"
            " from cases c where c.status = 'escalated' and c.archived_at is null"
            " order by c.updated_at limit %s",
            (MAX_ITEMS,),
        ).fetchall()


def channels(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Channels:
    installed = next(
        (s.done for s in first_steps.steps(conn, tenant_id) if s.key == "widget"), False
    )
    setup = whatsapp.load(conn, tenant_id)
    return Channels(
        web_installed=installed,
        whatsapp=(setup.status if setup else ""),
        store=system.mode_for(conn, tenant_id),
    )


def agent_state(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    """`live`, `paused` (salió en vivo y lo pausaron) o `setup` (nunca salió)."""
    if go_live.is_live(conn, tenant_id):
        return "live"
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ? 'live' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    return "paused" if row and row[0] else "setup"


# -- pantalla ------------------------------------------------------------------------------------
def _steps_html(found: list[first_steps.Step]) -> str:
    done = sum(s.done for s in found)
    if done == len(found):
        return ""
    current = next(i for i, s in enumerate(found) if not s.done)
    items = "".join(
        f"""<li class="step{" done" if s.done else ""}{" current" if i == current else ""}">
<span class="dot" aria-hidden="true">{"✓" if s.done else i + 1}</span>
<div class="grow"><a href="{s.link}"><b>{esc(s.title)}</b></a>
<div class="muted small">{esc(s.detail)}</div></div>
{
            '<span class="badge ok">Done</span>'
            if s.done
            else f'<a class="btn{" primary" if i == current else ""}" href="{s.link}">Start</a>'
        }</li>"""
        for i, s in enumerate(found)
    )
    percent = round(100 * done / len(found))
    return f"""<section class="card" aria-labelledby="get-started">
<div class="row"><h2 id="get-started" class="grow" style="margin:0">Get started</h2>
<span class="muted small">{done} of {len(found)} done</span></div>
<p class="muted small" style="margin:4px 0 10px">Your agent goes live when these steps are done.</p>
<div class="track" role="progressbar" aria-valuenow="{percent}" aria-valuemin="0"
 aria-valuemax="100" aria-label="Setup progress"><div class="fill ok"
 style="width:{percent}%"></div></div>
<ol class="checklist">{items}</ol></section>"""


def _attention_html(
    ctx: Ctx, approvals: list[tuple[Any, ...]], escalated: list[tuple[Any, ...]], drafts: list[Any]
) -> str:
    rows: list[str] = []
    for approval_id, tool, params, case_id, created, texts in approvals[:MAX_ITEMS]:
        rows.append(
            f"""<li class="item"><div class="grow"><span class="badge waiting_approval">Approval</span><br>
<b>{esc(action_label(tool, params))}</b>
<div class="muted small ellipsis">“{esc(summary(texts or []) or "")}” ·
<a href="/panel/casos/{case_id}">View conversation</a> · {local_time(created)}</div></div>
<form method="post" action="/panel/aprobaciones/{approval_id}" class="row" style="gap:6px">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<input type="hidden" name="volver" value="/panel/home">
<button class="danger" name="decision" value="rechazar">Reject</button>
<button class="primary" name="decision" value="aprobar">Approve</button></form></li>"""
        )
    for case_id, updated, texts in escalated:
        rows.append(
            f"""<li class="item"><div class="grow"><span class="badge escalated">Handed off</span><br>
<b>A customer is waiting for your team</b>
<div class="muted small ellipsis">“{esc(summary(texts or []) or "")}” · {local_time(updated)}</div>
</div><a class="btn primary" href="/panel/casos/{case_id}">Reply</a></li>"""
        )
    for draft in drafts[:MAX_ITEMS]:
        rows.append(
            f"""<li class="item"><div class="grow"><span class="badge info">Draft</span><br>
<b>Draft to review</b>
<div class="muted small ellipsis">“{esc(draft.text)}” · {local_time(draft.created_at)}</div>
</div><a class="btn primary" href="/panel/casos/{draft.case_id}">Review</a></li>"""
        )
    total = len(approvals) + len(escalated) + len(drafts)
    count = f'<span class="badge warn">{total}</span>' if total else ""
    body = (
        f'<ul class="items">{"".join(rows)}</ul>'
        if rows
        else '<div class="empty small"><b>You\'re all caught up</b>Nothing needs you right now. '
        "Approvals, hand-offs and drafts show up here.</div>"
    )
    return f"""<section class="card" aria-labelledby="attention">
<div class="row"><h2 id="attention" class="grow" style="margin:0">Needs your attention</h2>
{count}<a class="small" href="/panel/inbox">Open Inbox</a></div>{body}</section>"""


def _sales(totals: list[attribution.SalesTotal]) -> str:
    sold = [t for t in totals if t.sales]
    if not sold:
        return money(0)
    return " · ".join(
        money(t.amount) if t.currency in ("USD", "ARS", "") else f"{t.currency} {money(t.amount)}"
        for t in sold
    )


def _week_html(found: Week) -> str:
    cost = (
        f"${found.cost:,.2f}"
        if english.english()
        else f"US$ {found.cost:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )
    tiles = (
        (str(found.conversations), "Conversations"),
        (str(found.resolved_by_agent), "Resolved by your agent"),
        (str(found.handed_off), "Handed off"),
        (_sales(found.sales), "Attributed sales"),
        (cost, "AI cost"),
    )
    kpis = "".join(
        f'<div class="kpi"><div class="n">{esc(value)}</div><div class="muted">{label}</div></div>'
        for value, label in tiles
    )
    return f"""<section aria-labelledby="week"><div class="row" style="margin:24px 0 10px">
<h2 id="week" class="grow" style="margin:0">Last 7 days</h2>
<a class="small" href="/panel/insights">See all insights</a></div>
<div class="kpis week">{kpis}</div></section>"""


STATE = {
    "live": ("live", "Live", "Your agent is answering your customers."),
    "paused": (
        "paused",
        "Paused",
        "Your agent isn't answering. Customers are told a person will reply.",
    ),
    "setup": ("setup", "Setting up", "Finish the steps above to go live."),
}


def _status_html(ctx: Ctx, state: str, found: Channels) -> str:
    css, label, help_text = STATE[state]
    toggle = ""
    if state == "live":
        toggle = f"""<form method="post" action="/panel/salir-en-vivo">
<input type="hidden" name="csrf" value="{ctx.csrf}"><input type="hidden" name="activo" value="no">
<input type="hidden" name="volver" value="/panel/home">
<button type="submit">Pause agent</button></form>"""
    elif state == "paused":
        toggle = '<a class="btn primary" href="/panel/deploy#go-live">Resume</a>'
    web = ("ok", "Installed") if found.web_installed else ("", "Not installed")
    wa = {"active": ("ok", "Connected"), "inactive": ("warn", "Not active")}.get(
        found.whatsapp, ("", "Not connected")
    )
    store = {
        "real": ("ok", "Connected"),
        "demo": ("ok", "Demo store"),
        "simulado": ("warn", "Test mode"),
    }.get(found.store, ("", "Not connected"))
    rows = "".join(
        f'<li><b>{name}</b><span class="badge {css_class}">{text}</span></li>'
        for name, (css_class, text) in (
            ("Website chat", web),
            ("WhatsApp", wa),
            ("Store", store),
        )
    )
    return f"""<section class="card" aria-labelledby="status">
<div class="row"><h2 id="status" class="grow" style="margin:0">Agent status</h2>
<span class="badge {css}">{label}</span></div>
<p class="muted small">{help_text}</p>{toggle}
<ul class="channels">{rows}</ul>
<a class="small" href="/panel/deploy">Manage channels</a></section>"""


HOME_CSS = """<style>
.home{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:16px;
align-items:start}.home>div{min-width:0}
@media (max-width:900px){.home{grid-template-columns:1fr}}
.checklist{list-style:none;padding:0;margin:12px 0 0}
.checklist .step{display:flex;gap:12px;align-items:center;padding:10px 0;
border-top:1px solid var(--line)}
.checklist .dot{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;
flex:none;font-size:12.5px;font-weight:700;background:var(--sunken);color:var(--muted)}
.checklist .done .dot{background:var(--ok-soft);color:var(--ok)}
.checklist .current .dot{background:var(--brand);color:var(--on-brand)}
.checklist .done b{color:var(--muted);font-weight:500}
.checklist a{color:inherit;text-decoration:none}
.items{list-style:none;padding:0;margin:10px 0 0}
.items .item{display:flex;gap:8px 12px;align-items:center;padding:12px 0;
border-top:1px solid var(--line);flex-wrap:wrap}.items .item>.grow{flex:1 1 240px}
.items .item .badge{margin-bottom:4px}
.kpis.week{grid-template-columns:repeat(5,minmax(0,1fr))}
@media (max-width:1100px){.kpis.week{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:560px){.kpis.week{grid-template-columns:repeat(2,minmax(0,1fr))}}
.kpis.week .n{font-size:22px}
.channels{list-style:none;padding:0;margin:12px 0 8px}
.channels li{display:flex;justify-content:space-between;gap:8px;padding:8px 0;
border-top:1px solid var(--line)}
</style>"""


@router.get("/home", response_class=HTMLResponse)
def home_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = first_steps.steps(conn, ctx.tenant_id)
        show_steps = not (ctx.demo or ctx.session.admin or ctx.session.role == "agent")
        approvals = _pending(conn, ctx.tenant_id)
        escalated = handoffs(conn, ctx.tenant_id)
        drafts = supervision.pending(conn, ctx.tenant_id)
        numbers = week(conn, ctx.tenant_id)
        state = agent_state(conn, ctx.tenant_id)
        current = channels(conn, ctx.tenant_id)
    steps = _steps_html(found) if show_steps else ""
    body = f"""{HOME_CSS}{toast(request.query_params.get("aviso"))}
<div class="pagehead"><div><h1>Home</h1>
<p class="lead">What your agent is doing and what needs you.</p></div></div>
<div class="home"><div>{steps}{_attention_html(ctx, approvals, escalated, drafts)}
{_week_html(numbers)}</div><div>{_status_html(ctx, state, current)}</div></div>"""
    return HTMLResponse(page("Home", body, active="home", session=ctx.session))
