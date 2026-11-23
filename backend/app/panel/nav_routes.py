"""Navegación del panel (Hito 13, decisión 085).

- Home, la pestaña Drafts de Inbox, y las pestañas Playground y Simulations de Test & Improve.
- Las direcciones de antes (`/panel/casos`, `/panel/configuracion`…) llevan a su pantalla nueva,
  con los mismos parámetros: siguen sirviendo los links de los emails y los marcadores.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.panel.html import page, toast
from app.panel.routes import Deps, _draft_cards, _first_steps_html, enter

router = APIRouter(prefix="/panel", include_in_schema=False)


@router.get("/home", response_class=HTMLResponse)
def home_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        steps = _first_steps_html(conn, ctx)
    body = f"""<div class="pagehead"><div><h1>Home</h1>
<p class="lead">What your agent is doing and what needs you.</p></div></div>{steps}"""
    return HTMLResponse(page("Home", body, active="home", session=ctx.session))


@router.get("/inbox/drafts", response_class=HTMLResponse)
def drafts_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        drafts = _draft_cards(conn, ctx.tenant_id, ctx.csrf, back="/panel/inbox/drafts")
    empty = """<div class="card empty"><b>No drafts waiting</b>
With review before sending on, every answer from your agent waits here until someone on your team
sends, edits or rejects it. <br><a class="btn" href="/panel/agent">Review settings</a></div>"""
    body = f"""{toast(request.query_params.get("aviso"))}<div class="pagehead"><div>
<h1>Drafts</h1><p class="lead">Answers your agent wrote that wait for a person before they reach
the customer.</p></div></div>{drafts or empty}"""
    return HTMLResponse(page("Drafts", body, active="inbox/drafts", session=ctx.session))


@router.get("/test/playground", response_class=HTMLResponse)
def playground_tab(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    body = """<div class="pagehead"><div><h1>Playground</h1><p class="lead">Chat with your agent
as a customer would. Next to the chat you see each step it takes: what it understood, what it
looked up and why it answered that way.</p></div>
<a class="btn" href="/panel/probar" target="_blank" rel="noopener">Open in a new tab</a></div>
<iframe src="/panel/probar" title="Playground" style="width:100%;height:76vh;min-height:520px;
border:1px solid var(--line);border-radius:12px;background:var(--surface)"></iframe>"""
    return HTMLResponse(page("Playground", body, active="test/playground", session=ctx.session))


@router.get("/test/simulations", response_class=HTMLResponse)
def simulations_tab(request: Request, deps: Deps) -> Response:
    from app.panel.simulation_routes import simulation_card

    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        card = simulation_card(ctx, conn)
    body = f"""<div class="pagehead"><div><h1>Simulations</h1><p class="lead">Simulated
customers talk to your agent before real ones do, and a reviewer checks every conversation.</p>
</div></div>{card}"""
    return HTMLResponse(page("Simulations", body, active="test/simulations", session=ctx.session))


# -- direcciones de antes ------------------------------------------------------------------------
LEGACY = {
    "casos": "/panel/inbox",
    "aprobaciones": "/panel/inbox/approvals",
    "onboarding": "/panel/knowledge",
    "configuracion": "/panel/agent",
    "reporte": "/panel/test",
    "mejorar": "/panel/test/improve",
    "metricas": "/panel/insights",
    "resultados": "/panel/insights/results",
    "clientes": "/panel/customers",
    "equipo": "/panel/settings",
    "empresas": "/panel/operator/companies",
    "pruebas": "/panel/operator/trials",
    "facturacion": "/panel/operator/billing",
}


def _legacy(slug: str) -> object:
    def redirect(request: Request) -> Response:
        query = request.url.query
        return RedirectResponse(LEGACY[slug] + (f"?{query}" if query else ""), status_code=303)

    redirect.__name__ = f"legacy_{slug}"
    return redirect


for _slug in LEGACY:
    router.add_api_route(f"/{_slug}", _legacy(_slug), methods=["GET"])  # type: ignore[arg-type]
