"""Registro self-service y alta por invitación (decisión 058). Páginas públicas: todavía no hay
sesión."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.companies import signup
from app.mailer import mailer_from_env
from app.panel import auth, english
from app.panel.html import auth_card, esc, page
from app.panel.routes import Deps, Form

router = APIRouter(include_in_schema=False)


def _base(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{request.headers.get('host') or request.url.netloc}"


def _language(request: Request, lang: str = "") -> None:
    """Antes de entrar no hay usuario: el idioma es el elegido en este navegador (decisión 085)."""
    from app.panel.routes import LANG_COOKIE

    english.set_language(auth.language_or_default(lang or request.cookies.get(LANG_COOKIE)))
    english.THEME.set("light")


def _signup_page(error: str = "", values: dict[str, str] | None = None) -> str:
    v = values or {}
    alert = f'<div class="note" role="alert">{esc(error)}</div>' if error else ""
    # Desde una prueba gratis (decisión 075): el nombre y el email ya vienen cargados.
    trial = v.get("prueba", "")
    hidden = f'<input type="hidden" name="prueba" value="{esc(trial)}">' if trial else ""
    locked = " readonly" if trial else ""
    lead = (
        "Tu piloto conserva las políticas y los productos que cargamos en la prueba."
        if trial
        else "Un agente que atiende a tus clientes con tus políticas y tu sistema."
    )
    body = f"""<h1>Creá la cuenta de tu empresa</h1>
<p class="lead">{lead}</p>{alert}
<form method="post" action="/registro">{hidden}
<label for="empresa">Nombre de la empresa</label>
<input id="empresa" name="empresa" required maxlength="80" value="{esc(v.get("empresa", ""))}">
<label for="email">Tu email</label>
<input id="email" name="email" type="email" required value="{esc(v.get("email", ""))}"{locked}>
<label for="clave">Clave (al menos {auth.MIN_PASSWORD} caracteres)</label>
<input id="clave" name="clave" type="password" required autocomplete="new-password">
<p><button class="primary" type="submit" style="width:100%">Crear cuenta</button></p></form>
<p class="muted small">Te mandamos un enlace para confirmar tu email. ¿Ya tenés cuenta?
<a href="/panel/login">Ingresá</a>.</p>"""
    return page("Crear cuenta", auth_card(body), logged_in=False)


@router.get("/registro", response_class=HTMLResponse)
def signup_form(request: Request, deps: Deps, prueba: str = "", lang: str = "") -> HTMLResponse:
    _language(request, lang)
    if not prueba:
        return HTMLResponse(_signup_page())
    from app.trial import service

    with deps.connection() as conn:
        trial = service.find(conn, prueba)
    if trial is None or trial.status not in service.ACTIVE:
        return HTMLResponse(_signup_page())
    values = {"empresa": trial.name, "email": trial.email, "prueba": prueba}
    return HTMLResponse(_signup_page(values=values))


@router.post("/registro", response_class=HTMLResponse)
def signup_submit(request: Request, form: Form, deps: Deps) -> HTMLResponse:
    _language(request)
    values = {k: form.get(k, "") for k in ("empresa", "email", "prueba")}
    try:
        with deps.connection() as conn:
            signup.start_signup(
                conn,
                mailer_from_env(),
                company=form.get("empresa", ""),
                email=form.get("email", ""),
                password=form.get("clave", ""),
                base_url=_base(request),
                trial_token=form.get("prueba", ""),
            )
    except signup.SignupError as error:
        return HTMLResponse(_signup_page(str(error), values), status_code=400)
    body = """<h1>Revisá tu email</h1>
<p>Te mandamos un enlace para confirmar tu email y crear la cuenta. Vence en 24 horas.</p>
<p class="muted small">Si no llega en unos minutos, revisá el correo no deseado.</p>"""
    return HTMLResponse(page("Revisá tu email", auth_card(body), logged_in=False))


def _login(request: Request, token: str, target: str) -> RedirectResponse:
    response = RedirectResponse(target, status_code=303)
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


@router.get("/registro/confirmar")
def signup_confirm(request: Request, token: str, deps: Deps) -> Response:
    _language(request)
    try:
        with deps.connection() as conn:
            company = signup.confirm_signup(conn, token)
            email = conn.execute(
                "select email from users where id = %s", (company.user_id,)
            ).fetchone()
    except signup.SignupError as error:
        return HTMLResponse(_signup_page(str(error)), status_code=400)
    assert email is not None
    session = auth.new_session(
        str(email[0]), tenant_id=company.tenant_id, company=company.name, admin=False
    )
    return _login(request, session, "/panel/home")


def _invite_page(token: str, error: str = "") -> str:
    alert = f'<div class="note" role="alert">{esc(error)}</div>' if error else ""
    body = f"""<h1>Elegí tu clave</h1>
{alert}<form method="post" action="/panel/invitacion">
<input type="hidden" name="token" value="{esc(token)}">
<label for="clave">Clave (al menos {auth.MIN_PASSWORD} caracteres)</label>
<input id="clave" name="clave" type="password" required autocomplete="new-password">
<p><button class="primary" type="submit" style="width:100%">Entrar al panel</button></p>
</form>"""
    return page("Invitación", auth_card(body), logged_in=False)


@router.get("/panel/invitacion", response_class=HTMLResponse)
def invitation_form(request: Request, token: str = "") -> HTMLResponse:
    _language(request)
    return HTMLResponse(_invite_page(token))


@router.post("/panel/invitacion")
def invitation_accept(request: Request, form: Form, deps: Deps) -> Response:
    _language(request)
    token = form.get("token", "")
    try:
        with deps.connection() as conn:
            member = signup.accept_invitation(conn, token, form.get("clave", ""))
    except signup.SignupError as error:
        return HTMLResponse(_invite_page(token, str(error)), status_code=400)
    session = auth.new_session(
        member.email,
        tenant_id=member.tenant_id,
        company=member.company,
        admin=False,
        role=member.role,
    )
    return _login(request, session, "/panel/home")
