"""Equipo de la empresa: quién usa el panel y con qué rol, e invitaciones (decisión 058)."""

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.companies import signup
from app.mailer import mailer_from_env
from app.panel.html import esc, page
from app.panel.routes import Deps, Form, enter
from app.panel.signup_routes import _base

router = APIRouter(prefix="/panel", include_in_schema=False)


@router.get("/equipo", response_class=HTMLResponse)
def team_page(request: Request, deps: Deps, ok: str = "", error: str = "") -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = signup.members(conn, ctx.tenant_id)
    rows = "".join(
        f"<tr><td class=mono>{esc(email)}</td><td>{esc(signup.ROLES.get(role, role))}</td>"
        f"<td>{'Invitación enviada' if pending else 'Activo'}</td></tr>"
        for email, role, pending in found
    )
    notice = (
        f'<div class="card okmsg">{esc(ok)}</div>'
        if ok
        else f'<div class="card alert">{esc(error)}</div>'
        if error
        else ""
    )
    options = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in signup.ROLES.items())
    body = f"""<h1>Equipo</h1><p class="lead">Quién usa el panel de {esc(ctx.session.company)}.</p>
{notice}<div class="card"><table><tr><th>Email</th><th>Rol</th><th>Estado</th></tr>{rows}</table>
</div><div class="card"><h2 style="margin-top:0">Invitar</h2>
<form method="post" action="/panel/equipo/invitar"><input type="hidden" name="csrf"
 value="{ctx.csrf}"><label for="email">Email</label>
<input id="email" name="email" type="email" required style="max-width:360px">
<label for="rol">Rol</label><select id="rol" name="rol" style="max-width:260px">{options}</select>
<p class="muted small">Un admin ve y cambia todo. Una persona del equipo atiende Casos y
Aprobaciones, pero no cambia la configuración.</p>
<button class="primary" type="submit">Enviar invitación</button></form></div>"""
    return HTMLResponse(page("Equipo", body, active="equipo", session=ctx.session))


@router.post("/equipo/invitar")
def team_invite(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        with deps.connection() as conn:
            signup.invite(
                conn,
                mailer_from_env(),
                ctx.tenant_id,
                company=ctx.session.company,
                email=form.get("email", ""),
                role=form.get("rol", ""),
                base_url=_base(request),
            )
    except signup.SignupError as error:
        return RedirectResponse(f"/panel/equipo?error={quote(str(error))}", status_code=303)
    return RedirectResponse("/panel/equipo?ok=" + quote("Invitación enviada."), status_code=303)
