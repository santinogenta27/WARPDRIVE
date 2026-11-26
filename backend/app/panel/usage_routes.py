"""Settings → Usage & model y Settings → Usage (decisiones 049 y 102).

- **Usage & model:** el proveedor y el modelo de una lista corta, "Test key" (una llamada mínima,
  con el error en palabras simples), la key enmascarada con "Replace" y "Remove", y el tope mensual.
  Una key que no pasó la prueba no se guarda. La key nunca vuelve al navegador.
- **Usage:** el gasto del mes desde la tabla `usage` de la empresa: proyección, tope, gasto por día,
  por modelo, tokens, costo por conversación y por caso resuelto, las conversaciones más caras y el
  CSV del mes. Es una estimación: el monto exacto está en la factura del proveedor.

Solo los admins de la empresa (Settings es solo para admins, decisión 058). Cada cambio queda en
`audit_events`, sin la key.
"""

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.audit import log
from app.llm import pricing, spending, tenant_llm
from app.panel.company_routes import LEADS, _back, _csrf, _notice, head
from app.panel.html import esc, page
from app.panel.routes import Ctx, Deps, Form, enter

router = APIRouter(include_in_schema=False)  # se suma al de company_routes (prefijo /panel)
PATH = "settings/usage"
SPEND_PATH = "settings/spending"
MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
LEADS_SPEND = (
    "What your agent spent this month, estimated from the tokens each call used and the "
    "published price of each model."
)

CSS = """
.usage-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;
margin-bottom:16px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
box-shadow:var(--shadow)}
.stat .n{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.stat .l{color:var(--muted);font-size:13px;margin-top:2px}
.stat .s{color:var(--muted);font-size:12.5px;margin-top:6px}
.meter{height:8px;border-radius:999px;background:var(--sunken);overflow:hidden;margin-top:10px}
.meter>span{display:block;height:100%;background:var(--brand);border-radius:inherit}
.meter.warn>span{background:var(--warn)}.meter.bad>span{background:var(--bad)}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
font-size:12.5px;font-weight:600;background:var(--brand-soft);color:var(--brand-ink)}
.pill.own{background:var(--ok-soft);color:var(--ok)}
.pill.warn{background:var(--warn-soft);color:var(--warn)}
.keyline{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}
.keyline .mono{font-size:14px}
.hint{color:var(--muted);font-size:13px;margin:6px 0 0}
.check{margin-top:12px;padding:10px 12px;border-radius:8px;font-size:14px}
.check.ok{background:var(--ok-soft);color:var(--ok)}.check.bad{background:var(--bad-soft);
color:var(--bad)}
.models{list-style:none;padding:0;margin:8px 0 0;font-size:13px;color:var(--muted)}
.models li{margin:3px 0}.models b{color:var(--ink-2);font-weight:600}
.chart{width:100%;height:auto;display:block;overflow:visible}
.chart .bar path{fill:var(--brand)}
.chart .bar:hover path,.chart .bar:focus path{fill:var(--brand-ink)}
.chart .bar:focus{outline:none}
.chart .axis{stroke:var(--line-strong);stroke-width:1}
.chart .grid{stroke:var(--line);stroke-width:1}
.chart text{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);font-size:12.5px;
padding:6px 9px;border-radius:6px;box-shadow:var(--shadow);z-index:20;white-space:nowrap}
.estimate{color:var(--muted);font-size:13px;margin-top:18px;max-width:80ch}
.unknown{color:var(--warn);font-weight:600}
"""


def _audit(conn: Any, ctx: Ctx, kind: str, **payload: Any) -> None:
    """Un cambio de la empresa (sin caso) en la auditoría. Nunca la key."""
    log.record(conn, ctx.tenant_id, None, kind, {"by": ctx.session.email, **payload})


def _usd(amount: Decimal | None) -> str:
    if amount is None:
        return "—"
    value = Decimal(amount)
    if value == 0:
        return "USD 0.00"
    if abs(value) < Decimal("0.0001"):
        return "< USD 0.0001"
    if abs(value) < Decimal("0.01"):
        return f"USD {value:.4f}"
    return f"USD {value:,.2f}"


def _int(value: int) -> str:
    return f"{value:,}"


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _billed(own: tenant_llm.OwnKey | None) -> str:
    if own is None:
        return '<span class="pill">Included in your plan</span>'
    account = tenant_llm.ACCOUNTS[own.provider]
    return f'<span class="pill own">Billed to your {esc(account)} account</span>'


def _meter(used: float) -> str:
    level = " bad" if used >= 1 else " warn" if used >= spending.WARN_AT else ""
    width = max(0, min(100, round(used * 100)))
    return (
        f'<div class="meter{level}" role="progressbar" aria-valuemin="0" aria-valuemax="100"'
        f' aria-valuenow="{width}" aria-label="Share of the monthly cap used">'
        f'<span style="width:{width}%"></span></div>'
    )


def _cap_text(limits: spending.Budget, spent: spending.Spend, used: float) -> str:
    if limits.cap_usd is None:
        return "No monthly cap"
    text = f"{used:.0%} of your {_usd(limits.cap_usd)} cap"
    if limits.cap_calls:
        text += f" · {spent.calls:,} of {limits.cap_calls:,} model calls"
    return text


# -- Settings → Usage & model -------------------------------------------------------------------
def _model_select(name: str, provider: str | None, chosen: str | None) -> str:
    groups = []
    for key, models in tenant_llm.CATALOG.items():
        if provider and key != provider:
            continue
        options = "".join(
            f'<option value="{esc(m.id)}" data-note="{esc(m.note)}"'
            f"{' selected' if m.id == chosen else ''}>{esc(m.label)}</option>"
            for m in models
        )
        groups.append(
            f'<optgroup label="{esc(tenant_llm.PROVIDERS[key])}" data-provider="{key}">'
            f"{options}</optgroup>"
        )
    return f'<select id="{name}" name="model" style="max-width:340px">{"".join(groups)}</select>'


def _catalog_notes() -> str:
    """Para qué sirve cada modelo (también sin JavaScript)."""
    items = "".join(
        f'<li data-model="{esc(m.id)}"><b>{esc(m.label)}</b>: <span>{esc(m.note)}</span></li>'
        for models in tenant_llm.CATALOG.values()
        for m in models
    )
    return f'<ul class="models" id="model-notes">{items}</ul>'


def _key_form(ctx: Ctx, own: tenant_llm.OwnKey | None) -> str:
    provider = own.provider if own else "anthropic"
    providers = "".join(
        f'<option value="{key}"{" selected" if key == provider else ""}>{esc(label)}</option>'
        for key, label in tenant_llm.PROVIDERS.items()
    )
    chosen = own.model if own else tenant_llm.default_model(provider)
    return f"""<form method="post" action="/panel/settings/usage/key" id="key-form"
 autocomplete="off">{_csrf(ctx)}
<label for="provider">Provider</label>
<select id="provider" name="provider" style="max-width:340px">{providers}</select>
<label for="model">Model</label>{_model_select("model", None, chosen)}
<p class="hint" id="model-note"></p>
<label for="key">API key</label>
<input id="key" name="key" type="password" autocomplete="new-password" spellcheck="false"
 placeholder="Paste your key" required style="max-width:440px">
<p class="hint">We test the key with a tiny request and save it encrypted. It's never shown
again.</p>
<div class="row"><button type="submit" formaction="/panel/settings/usage/test"
 id="test-key">Test key</button><button type="submit" class="primary">Save key</button></div>
<div id="key-check" role="status" aria-live="polite"></div>
{_catalog_notes()}</form>"""


KEY_JS = """
(function(){
  var form=document.getElementById("key-form");if(!form)return;
  var provider=form.querySelector("#provider"),model=form.querySelector("#model");
  var note=document.getElementById("model-note"),list=document.getElementById("model-notes");
  if(list)list.hidden=true;
  function sync(){
    var groups=model.querySelectorAll("optgroup"),first=null;
    groups.forEach(function(g){var on=g.dataset.provider===provider.value;g.hidden=!on;
      g.disabled=!on;if(on&&!first)first=g.querySelector("option")});
    var current=model.selectedOptions[0];
    if(!current||current.parentNode.disabled){model.value=first?first.value:""}
    var chosen=model.selectedOptions[0],item=chosen&&list?
      list.querySelector('li[data-model="'+chosen.value+'"] span'):null;
    note.textContent=item?item.textContent:"";
  }
  provider.addEventListener("change",sync);model.addEventListener("change",sync);sync();
  var box=document.getElementById("key-check"),button=document.getElementById("test-key");
  button.addEventListener("click",function(e){
    if(!form.reportValidity())return;e.preventDefault();
    box.className="check";box.textContent="Testing…";button.disabled=true;
    fetch(button.getAttribute("formaction"),{method:"POST",
      body:new URLSearchParams(new FormData(form)),
      headers:{"Accept":"application/json"}}).then(function(r){return r.json()})
    .then(function(d){box.className="check "+(d.ok?"ok":"bad");box.textContent=d.message})
    .catch(function(){box.className="check bad";
      box.textContent="We couldn't run the test. Try again."})
    .finally(function(){button.disabled=false});
  });
})();
"""


def _model_card(ctx: Ctx, own: tenant_llm.OwnKey | None, limits: spending.Budget) -> str:
    embeddings = (
        "Search over your documents keeps working as it does today, with the platform's search, "
        "so nothing needs to be processed again."
    )
    if own is None:
        limit = []
        if limits.cap_usd is not None:
            limit.append(f"{_usd(limits.cap_usd)} a month")
        if limits.cap_calls:
            limit.append(f"{limits.cap_calls:,} model calls a month")
        limited = f"<span>Limit: {esc(' and '.join(limit))}.</span>" if limit else ""
        return f"""<div class="card"><div class="keyline"><b>Platform model</b>
{_billed(None)}</div>
<p class="hint"><span>Your agent uses our model, included in your plan.</span> {limited}
<span>Add your own key to use your provider account, with no platform limits.</span></p>
<h3 style="margin:18px 0 0">Use your own key</h3>{_key_form(ctx, None)}
<p class="hint">{esc(embeddings)}</p></div>"""
    label = tenant_llm.model_label(own.provider, own.model)
    light = tenant_llm.model_label(own.provider, tenant_llm.LIGHT[own.provider])
    return f"""<div class="card"><div class="keyline">
<b>{esc(tenant_llm.PROVIDERS[own.provider])}</b><span>{esc(label)}</span>
<span class="mono" aria-label="Saved key">{esc(own.hint)}</span>{_billed(own)}</div>
<p class="hint">Conversations use {esc(label)}. Classifying messages and checking answers use
{esc(light)}, the provider's fast model.</p>
<form method="post" action="/panel/settings/usage/model" class="row" style="align-items:flex-end">
{_csrf(ctx)}<div><label for="change-model">Model</label>
{_model_select("change-model", own.provider, own.model)}</div>
<button type="submit">Change model</button></form>
<div class="row" style="margin-top:14px"><a class="btn" href="#replace"
 onclick="document.getElementById('replace').open=true">Replace</a>
<form method="post" action="/panel/settings/usage/remove" style="margin:0">{_csrf(ctx)}
<button type="submit" class="danger">Remove</button></form></div>
<p class="hint">Removing the key switches your agent back to the platform model, with its
limits.</p>
<details id="replace" style="margin-top:12px"><summary>Replace the key</summary>
{_key_form(ctx, own)}</details>
<p class="hint">{esc(embeddings)}</p></div>"""


def _cap_card(ctx: Ctx, conn: Any, limits: spending.Budget, own: bool) -> str:
    chosen = spending._setting(conn, ctx.tenant_id)
    value = esc(str(chosen) if chosen is not None else "")
    who = (
        "With your own key there's no cap unless you set one."
        if own
        else "With the platform model, the platform limit applies even if you set a higher cap."
    )
    return f"""<div class="card"><form method="post" action="/panel/settings/usage/cap">
{_csrf(ctx)}<label for="cap">Monthly spending cap (USD)</label>
<div class="row" style="align-items:center"><input id="cap" name="cap" inputmode="decimal"
 value="{value}" placeholder="No cap" style="max-width:160px">
<button type="submit">Save cap</button></div>
<p class="hint"><span>At 80% we send a warning. At 100%, your agent hands every message to your
team until next month or until you raise the cap.</span> <span>{esc(who)}</span></p></form></div>"""


@router.get("/settings/usage", response_class=HTMLResponse)
def usage_model_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        own = tenant_llm.own_key(conn, ctx.tenant_id)
        limits = spending.budget(conn, ctx.tenant_id)
        spent = spending.month_spend(conn, ctx.tenant_id)
        used = spending.ratio(spent, limits)
        cap = _cap_card(ctx, conn, limits, own is not None)
    warn = '<span class="pill warn">Over 80% of the cap</span>' if used >= spending.WARN_AT else ""
    summary = f"""<div class="card"><div class="keyline"><b>This month</b>
<span class="mono">{_usd(spent.usd)}</span>{_billed(own)}{warn}
<a href="/panel/settings/spending" style="margin-left:auto">See usage</a></div>
{_meter(used)}<p class="hint">{esc(_cap_text(limits, spent, used))}</p></div>"""
    body = f"""<style>{CSS}</style>{head("Usage & model", LEADS["Usage & model"])}
{summary}<h2>AI model</h2>{_model_card(ctx, own, limits)}
<h2>Spending cap</h2>{cap}<script>{KEY_JS}</script>"""
    return HTMLResponse(
        page("Usage & model", _notice(request) + body, active=PATH, session=ctx.session)
    )


def _check_page(request: Request, ctx: Ctx, result: tenant_llm.KeyCheck) -> Response:
    if _wants_json(request):
        return JSONResponse({"ok": result.ok, "code": result.code, "message": result.message})
    if result.ok:
        return _back(PATH, ok=result.message)
    return _back(PATH, error=result.message)


@router.post("/settings/usage/test")
def test_key(request: Request, form: Form, deps: Deps) -> Response:
    """ "Test key": una llamada mínima. No guarda nada."""
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        result = tenant_llm.check_key(
            conn, ctx.tenant_id, form.get("provider", ""), form.get("key", ""), form.get("model")
        )
    return _check_page(request, ctx, result)


@router.post("/settings/usage/key")
def save_key(request: Request, form: Form, deps: Deps) -> Response:
    """ "Save key": la prueba (salvo que se haya probado bien recién) y, si pasó, la guarda."""
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    provider, key, model = form.get("provider", ""), form.get("key", ""), form.get("model")
    if provider not in tenant_llm.PROVIDERS:
        return _back(PATH, error="Choose a provider from the list.")
    chosen = model or tenant_llm.default_model(provider)
    with deps.connection() as conn:
        if tenant_llm.recently_tested(ctx.tenant_id, provider, chosen, key.strip()):
            result = tenant_llm.KeyCheck(True, "connected", "Connected.")
        else:
            result = tenant_llm.check_key(conn, ctx.tenant_id, provider, key, chosen)
        if not result.ok:
            return _back(PATH, error=f"{result.message} The key wasn't saved.")
        tenant_llm.save_key(conn, ctx.tenant_id, provider, key, chosen)
        _audit(conn, ctx, "llm_key_saved", provider=provider, model=chosen)
    label = tenant_llm.model_label(provider, chosen)
    return _back(PATH, ok=f"Connected. Your agent now uses {label} with your key.")


@router.post("/settings/usage/model")
def change_model(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    model = form.get("model", "")
    with deps.connection() as conn:
        own = tenant_llm.own_key(conn, ctx.tenant_id)
        if own is None:
            return _back(PATH, error="There's no key saved. Add one first.")
        if model not in {m.id for m in tenant_llm.CATALOG[own.provider]}:
            return _back(PATH, error="Choose a model from the list.")
        result = tenant_llm.set_model(conn, ctx.tenant_id, model)
        if not result.ok:
            return _back(PATH, error=f"{result.message} The model wasn't changed.")
        _audit(conn, ctx, "llm_model_changed", provider=own.provider, model=model, before=own.model)
    label = tenant_llm.model_label(own.provider, model)
    return _back(PATH, ok=f"Connected. Your agent now uses {label}.")


@router.post("/settings/usage/remove")
def remove_key(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        own = tenant_llm.own_key(conn, ctx.tenant_id)
        if own is None:
            return _back(PATH)
        tenant_llm.clear_key(conn, ctx.tenant_id)
        _audit(conn, ctx, "llm_key_removed", provider=own.provider, model=own.model)
    return _back(PATH, ok="Key removed. Your agent uses the platform model again, with its limits.")


@router.post("/settings/usage/cap")
def save_cap(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    raw = form.get("cap", "").strip().replace(",", ".").removeprefix("$")
    try:
        cap = Decimal(raw) if raw else None
    except InvalidOperation:
        return _back(PATH, error="The cap has to be a number, like 25.")
    if cap is not None and (cap < 0 or cap > Decimal("1000000")):
        return _back(PATH, error="The cap has to be between 0 and 1,000,000.")
    with deps.connection() as conn:
        before = spending._setting(conn, ctx.tenant_id)
        spending.set_budget(conn, ctx.tenant_id, cap)
        _audit(
            conn,
            ctx,
            "llm_cap_changed",
            cap_usd=str(cap) if cap is not None else None,
            before=str(before) if before is not None else None,
        )
    return _back(PATH, ok="Cap saved." if cap is not None else "Cap removed.")


# -- Settings → Usage -----------------------------------------------------------------------------
def _month(value: str) -> tuple[int, int]:
    today = datetime.now(ZoneInfo(spending.TZ)).date()
    try:
        year, month = (int(p) for p in value.split("-", 1))
        if 2020 <= year <= today.year + 1 and 1 <= month <= 12:
            return year, month
    except ValueError:
        pass
    return today.year, today.month


def _shift(year: int, month: int, delta: int) -> str:
    index = year * 12 + month - 1 + delta
    return f"{index // 12}-{index % 12 + 1:02d}"


def _chart(report: spending.Report) -> str:
    """El gasto por día: barras finas con la punta redondeada, una sola serie (sin leyenda), un
    tooltip por barra y la tabla al lado (dataviz: forma, marcas, hover y accesibilidad)."""
    start, end = spending._bounds(report.year, report.month)
    total_days = (end - start).days
    values = {d.day: d for d in report.days}
    top = max((d.usd for d in report.days), default=Decimal(0))
    width, height, left, bottom, top_pad = 720, 190, 52, 24, 10
    plot_w, plot_h = width - left - 8, height - bottom - top_pad
    step = plot_w / total_days
    bar = max(2.0, step - 2)  # 2 px de separación entre barras
    base = top_pad + plot_h
    bars = []
    for n in range(total_days):
        day = date(report.year, report.month, n + 1)
        found = values.get(day)
        usd = found.usd if found else Decimal(0)
        h = float(usd / top) * plot_h if top > 0 else 0.0
        x = left + n * step + (step - bar) / 2
        y = base - h
        r = min(4.0, bar / 2, h)
        if h <= 0:
            continue
        path = (
            f"M{x:.1f},{base:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
            f"H{x + bar - r:.1f}Q{x + bar:.1f},{y:.1f} {x + bar:.1f},{y + r:.1f}V{base:.1f}Z"
        )
        calls = found.calls if found else 0
        tip = f"{MONTHS[report.month - 1][:3]} {n + 1}: {_usd(usd)} · {calls:,} calls"
        bars.append(
            f'<g class="bar" tabindex="0" data-tip="{esc(tip)}"><title>{esc(tip)}</title>'
            f'<rect x="{left + n * step:.1f}" y="{top_pad}" width="{step:.1f}" height="{plot_h}"'
            f' fill="transparent"/><path d="{path}"/></g>'
        )
    ticks = "".join(
        f'<text x="{left + (n - 1) * step + step / 2:.1f}" y="{height - 6}"'
        f' text-anchor="middle">{n}</text>'
        for n in range(1, total_days + 1)
        if n == 1 or n % 5 == 0
    )
    y_labels = (
        f'<text x="{left - 6}" y="{base + 4:.1f}" text-anchor="end">$0</text>'
        f'<text x="{left - 6}" y="{top_pad + 4}" text-anchor="end">'
        f"{esc(_usd(top).removeprefix('USD '))}</text>"
        f'<line class="grid" x1="{left}" x2="{width - 8}" y1="{top_pad}" y2="{top_pad}"/>'
    )
    empty = (
        f'<text x="{left + plot_w / 2:.1f}" y="{top_pad + plot_h / 2:.1f}" text-anchor="middle">'
        "No spending yet this month</text>"
        if not bars
        else ""
    )
    svg = (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img"'
        f' aria-label="Spend per day in {MONTHS[report.month - 1]}">{y_labels}'
        f'<line class="axis" x1="{left}" x2="{width - 8}" y1="{base:.1f}" y2="{base:.1f}"/>'
        f"{''.join(bars)}{ticks}{empty}</svg>"
    )
    rows = "".join(
        f"<tr><td>{d.day:%b %d}</td><td class='num'>{d.calls:,}</td>"
        f"<td class='num'>{esc(_usd(d.usd))}</td></tr>"
        for d in report.days
    )
    table = (
        f"<details><summary>Show as a table</summary><table><tr><th>Day</th>"
        f"<th class='num'>Calls</th><th class='num'>Cost</th></tr>{rows}</table></details>"
        if rows
        else ""
    )
    return svg + table


TIP_JS = """
(function(){
  var tip=null;
  function show(e){var g=e.target.closest&&e.target.closest(".bar");if(!g)return;
    if(!tip){tip=document.createElement("div");tip.className="tip";document.body.appendChild(tip)}
    tip.textContent=g.dataset.tip;var r=g.getBoundingClientRect();
    tip.style.left=Math.max(8,r.left+r.width/2-tip.offsetWidth/2)+"px";
    tip.style.top=Math.max(8,r.top-tip.offsetHeight-8)+"px";tip.hidden=false}
  function hide(){if(tip)tip.hidden=true}
  document.querySelectorAll(".chart .bar").forEach(function(g){
    g.addEventListener("mouseenter",show);g.addEventListener("focus",show);
    g.addEventListener("mouseleave",hide);g.addEventListener("blur",hide);
    var t=g.querySelector("title");if(t)t.remove();
  });
})();
"""


def _model_name(model: str) -> tuple[str, str]:
    """(nombre para mostrar, de quién es el gasto)."""
    billed = tenant_llm.from_billed(model)
    if billed:
        provider, real = billed
        account = tenant_llm.ACCOUNTS[provider]
        return tenant_llm.model_label(provider, real), f"Your {account} account"
    return tenant_llm.display_name(model), "Included in your plan"


def _stat(value: str, label: str, sub: str = "", extra: str = "") -> str:
    small = f'<div class="s">{sub}</div>' if sub else ""
    return (
        f'<div class="stat"><div class="n">{esc(value)}</div><div class="l">{esc(label)}</div>'
        f"{extra}{small}</div>"
    )


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head_cells = "".join(
        f'<th class="{"num" if h.startswith(">") else ""}">{esc(h.lstrip(">"))}</th>'
        for h in headers
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="{"num" if headers[i].startswith(">") else ""}">{cell}</td>'
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head_cells}</tr>{body}</table>"


@router.get("/settings/spending", response_class=HTMLResponse)
def spending_page(request: Request, deps: Deps, mes: str = "") -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    year, month = _month(mes)
    with deps.connection() as conn:
        report = spending.month_report(conn, ctx.tenant_id, year, month)
        own = tenant_llm.own_key(conn, ctx.tenant_id)
    title = f"{MONTHS[month - 1]} {year}"
    current = report.projection is not None
    switch = (
        f'<div class="row" style="align-items:center;gap:8px">'
        f'<a class="btn" href="?mes={_shift(year, month, -1)}" aria-label="Previous'
        f' month">‹</a><b>{esc(title)}</b>'
        + (
            ""
            if current
            else f'<a class="btn" href="?mes={_shift(year, month, 1)}" aria-label="Next'
            ' month">›</a>'
        )
        + f'<a class="btn primary" href="/panel/settings/spending.csv?mes={year}-{month:02d}">'
        "Export CSV</a></div>"
    )
    limits, used = report.budget, report.ratio
    cap_extra = _meter(used) if current and limits.cap_usd is not None else ""
    cap_sub = esc(_cap_text(limits, report.spend, used)) if current else ""
    warn = (
        '<p class="pill warn" role="status">You passed 80% of your monthly cap.</p>'
        if current and used >= spending.WARN_AT
        else ""
    )
    stats = "".join(
        [
            _stat(_usd(report.total_usd), "Spent this month" if current else f"Spent in {title}"),
            _stat(_usd(report.projection), "Projected for the month") if current else "",
            _stat(
                _usd(limits.cap_usd) if limits.cap_usd is not None else "No cap",
                "Monthly cap",
                cap_sub if limits.cap_usd is not None else "Set one in Usage & model",
                cap_extra,
            )
            if current
            else "",
            _stat(
                _usd(report.per_conversation),
                "Average per conversation",
                f"{report.conversations:,} conversations",
            ),
            _stat(
                _usd(report.per_resolved),
                "Average per resolved case",
                f"{report.resolved:,} resolved",
            ),
            _stat(_int(report.input_tokens), "Input tokens"),
            _stat(_int(report.output_tokens), "Output tokens"),
        ]
    )
    model_rows = []
    for m in report.models:
        name, billed = _model_name(m.model)
        cost = (
            esc(_usd(m.usd))
            if m.known
            else '<span class="unknown" title="This model has no price in our table">'
            "price unknown</span>"
        )
        model_rows.append(
            [
                f'<span title="{esc(m.model)}">{esc(name)}</span>'
                + (
                    " <span class='muted small'>(document search)</span>"
                    if m.kind == "embedding"
                    else ""
                ),
                esc(billed),
                _int(m.calls),
                _int(m.input_tokens),
                _int(m.output_tokens),
                cost,
            ]
        )
    case_rows = [
        [
            f'<a class="mono" href="/panel/casos/{c.case_id}">{esc(str(c.case_id)[:8])}</a>',
            _int(c.calls),
            _int(c.tokens),
            esc(_usd(c.usd)),
        ]
        for c in report.top_cases
    ]
    unknown = (
        f'<p class="unknown">{report.unknown_calls:,} calls used a model with no price in our'
        " table. They show as price unknown and aren't included in the totals.</p>"
        if report.unknown_calls
        else ""
    )
    checked = pricing.last_checked()
    body = f"""<style>{CSS}</style>{head("Usage", LEADS_SPEND, switch)}
<div class="keyline" style="margin-bottom:14px">{_billed(own)}{warn}</div>
<div class="usage-grid">{stats}</div>
<div class="card"><h2 style="margin-top:0">Spend per day</h2>{_chart(report)}</div>
<div class="card"><h2 style="margin-top:0">By model</h2>{unknown}
{
        _table(
            ["Model", "Billed to", ">Calls", ">Input tokens", ">Output tokens", ">Cost"],
            model_rows,
            "No model calls this month.",
        )
    }</div>
<div class="card"><h2 style="margin-top:0">Most expensive conversations</h2>
{_table(["Case", ">Calls", ">Tokens", ">Cost"], case_rows, "No conversations this month.")}
</div>
<p class="estimate"><span>These amounts are an estimate, calculated from each model's published
price.</span> <span>Prices last checked on {checked:%B} {checked.day}, {checked.year}.</span>
<span>The exact amount is on your provider's invoice.</span></p>
<script>{TIP_JS}</script>"""
    return HTMLResponse(
        page("Usage", _notice(request) + body, active=SPEND_PATH, session=ctx.session)
    )


@router.get("/settings/spending.csv")
def spending_csv(request: Request, deps: Deps, mes: str = "") -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    year, month = _month(mes)
    with deps.connection() as conn:
        rows = spending.month_rows(conn, ctx.tenant_id, year, month)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "time",
            "kind",
            "model",
            "billed_to",
            "case_id",
            "input_tokens",
            "output_tokens",
            "cache_write_tokens",
            "cache_read_tokens",
            "cost_usd",
        ]
    )
    for when, kind, model, case_id, i, o, cw, cr, cost in rows:
        billed = tenant_llm.from_billed(model)
        writer.writerow(
            [
                when.strftime("%Y-%m-%d %H:%M:%S"),
                kind,
                model,
                f"your {billed[0]} account" if billed else "included in plan",
                str(case_id) if case_id else "",
                i,
                o,
                cw,
                cr,
                f"{Decimal(cost):f}" if pricing.known(model) else "price unknown",
            ]
        )
    name = f"usage-{year}-{month:02d}.csv"
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
