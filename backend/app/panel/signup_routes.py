"""Registro self-service y alta por invitación (decisión 058). Páginas públicas: todavía no hay
sesión."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.companies import signup
from app.mailer import mailer_from_env
from app.panel import auth
from app.panel.html import esc, page
from app.panel.routes import Deps, Form

router = APIRouter(include_in_schema=False)


def _base(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{request.headers.get('host') or request.url.netloc}"


def _signup_page(error: str = "", values: dict[str, str] | None = None) -> str:
    v = values or {}
    alert = f'<p style="color:var(--bad)">{esc(error)}</p>' if error else ""
    body = f"""<div class="card" style="max-width:440px;margin:40px auto">
<h1>Creá la cuenta de tu empresa</h1>
<p class="lead">Un asistente que atiende a tus clientes con tus políticas y tu sistema.</p>{alert}
<form method="post" action="/registro">
<label for="empresa">Nombre de la empresa</label>
<input id="empresa" name="empresa" required maxlength="80" value="{esc(v.get("empresa", ""))}">
<label for="email">Tu email</label>
<input id="email" name="email" type="email" required value="{esc(v.get("email", ""))}">
<label for="clave">Clave (al menos {auth.MIN_PASSWORD} caracteres)</label>
<input id="clave" name="clave" type="password" required autocomplete="new-password">
<p><button class="primary" type="submit" style="width:100%">Crear cuenta</button></p></form>
<p class="muted small">Te mandamos un enlace para confirmar tu email. ¿Ya tenés cuenta?
<a href="/panel/login">Ingresá</a>.</p></div>"""
    return page("Crear cuenta", body, logged_in=False)


@router.get("/registro", response_class=HTMLResponse)
def signup_form() -> HTMLResponse:
    return HTMLResponse(_signup_page())


@router.post("/registro", response_class=HTMLResponse)
def signup_submit(request: Request, form: Form, deps: Deps) -> HTMLResponse:
    values = {k: form.get(k, "") for k in ("empresa", "email")}
    try:
        with deps.connection() as conn:
            signup.start_signup(
                conn,
                mailer_from_env(),
                company=form.get("empresa", ""),
                email=form.get("email", ""),
                password=form.get("clave", ""),
                base_url=_base(request),
            )
    except signup.SignupError as error:
        return HTMLResponse(_signup_page(str(error), values), status_code=400)
    body = """<div class="card" style="max-width:440px;margin:40px auto"><h1>Revisá tu email</h1>
<p>Te mandamos un enlace para confirmar tu email y crear la cuenta. Vence en 24 horas.</p>
<p class="muted small">Si no llega en unos minutos, revisá el correo no deseado.</p></div>"""
    return HTMLResponse(page("Revisá tu email", body, logged_in=False))


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
    return _login(request, session, "/panel/configuracion")


def _invite_page(token: str, error: str = "") -> str:
    alert = f'<p style="color:var(--bad)">{esc(error)}</p>' if error else ""
    body = f"""<div class="card" style="max-width:400px;margin:40px auto"><h1>Elegí tu clave</h1>
{alert}<form method="post" action="/panel/invitacion">
<input type="hidden" name="token" value="{esc(token)}">
<label for="clave">Clave (al menos {auth.MIN_PASSWORD} caracteres)</label>
<input id="clave" name="clave" type="password" required autocomplete="new-password">
<p><button class="primary" type="submit" style="width:100%">Entrar al panel</button></p>
</form></div>"""
    return page("Invitación", body, logged_in=False)


@router.get("/panel/invitacion", response_class=HTMLResponse)
def invitation_form(token: str = "") -> HTMLResponse:
    return HTMLResponse(_invite_page(token))


@router.post("/panel/invitacion")
def invitation_accept(request: Request, form: Form, deps: Deps) -> Response:
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
    return _login(request, session, "/panel/casos")
