"""Revisión de conversaciones (Hito 11, decisión 073).

- `POST /panel/casos/{caso}/revisar`: marca una respuesta del asistente como correcta o
  incorrecta, con motivo y comentario.
- `GET /panel/mejorar`: las incorrectas que todavía no son un caso de eval.
- `POST /panel/mejorar/{revisión}/eval`: la convierte en un caso de eval de la empresa con la
  respuesta esperada que escribe el equipo.

Cualquier persona del equipo puede revisar (no es una sección solo para admins).
"""

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.cases import feedback
from app.panel.html import esc, local_time, page, toast
from app.panel.labels import type_label
from app.panel.privacy import mask_codes
from app.panel.routes import Ctx, Deps, Form, _not_found, _user_id, enter

router = APIRouter(prefix="/panel", include_in_schema=False)


def review_form(
    ctx: Ctx, case_id: uuid.UUID, message_id: uuid.UUID, found: feedback.Mark | None
) -> str:
    """Debajo de cada respuesta del asistente en el detalle del caso."""
    state = ""
    if found is not None:
        if found.verdict == "correct":
            state = '<span class="badge resolved">Marcada como correcta</span>'
        else:
            reason = feedback.REASONS.get(found.reason or "", "")
            state = '<span class="badge escalated">Marcada como incorrecta</span>' + (
                f" <span>{esc(reason)}</span>" if reason else ""
            )
        if found.comment:
            state += f' <span class="muted">“{esc(found.comment)}”</span>'
    reasons = "".join(
        f'<option value="{key}"{" selected" if found and found.reason == key else ""}>'
        f"{esc(label)}</option>"
        for key, label in feedback.REASONS.items()
    )
    wrong = found is not None and found.verdict == "incorrect"
    return f"""<div class="review small" style="margin:4px 0 12px">{state}
<form method="post" action="/panel/casos/{case_id}/revisar" class="row"
 style="gap:6px;margin-top:4px">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<input type="hidden" name="mensaje" value="{message_id}">
<select name="veredicto" aria-label="Revisión de la respuesta" style="max-width:140px">
<option value="correcta">Correcta</option>
<option value="incorrecta"{" selected" if wrong else ""}>Incorrecta</option></select>
<select name="motivo" aria-label="Motivo" style="max-width:240px">
<option value="">Motivo (si es incorrecta)</option>{reasons}</select>
<input name="comentario" maxlength="{feedback.MAX_COMMENT}" placeholder="Comentario"
 aria-label="Comentario" value="{esc(found.comment if found else "")}" style="max-width:280px">
<button type="submit">Guardar revisión</button></form></div>"""


@router.post("/casos/{case_id}/revisar")
def review_reply(case_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    try:
        message_id = uuid.UUID(form.get("mensaje", ""))
    except ValueError:
        return _not_found("Esa respuesta", ctx.session)
    with deps.connection() as conn:
        user_id = _user_id(conn, ctx.tenant_id, ctx.session.email)
        try:
            feedback.mark(
                conn,
                ctx.tenant_id,
                case_id,
                message_id,
                verdict=form.get("veredicto", ""),
                reason=form.get("motivo", ""),
                comment=form.get("comentario", ""),
                reviewed_by=user_id,
            )
        except feedback.ReviewError:
            return _not_found("Esa respuesta", ctx.session)
    return RedirectResponse(f"/panel/casos/{case_id}?aviso=revisada", status_code=303)


def _suggestion(item: feedback.ToImprove) -> str:
    """Información faltante: qué documento cargar (sin llamar a un modelo)."""
    if item.reason != "falta_informacion":
        return ""
    topic = (
        f'<div class="muted small">Tema: <span>{esc(type_label(item.case_type))}</span></div>'
        if item.case_type
        else ""
    )
    searched = (
        '<div class="muted small">La búsqueda no encontró ningún documento sobre esto.</div>'
        if item.no_sources
        else ""
    )
    return f"""<div class="card" style="margin:8px 0;background:var(--soft, transparent)">
<b>Cargá un documento que responda esta pregunta (por ejemplo, la política o la página de ayuda
del tema).</b>{topic}{searched}
<a href="/panel/knowledge">Subir documentos</a></div>"""


@router.get("/test/improve", response_class=HTMLResponse)
def improve_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        items = feedback.to_improve(conn, ctx.tenant_id)
    cards = (
        "".join(
            f"""<div class="card"><div class="row" style="gap:8px">
<span class="badge escalated">Incorrecta</span>
{f"<span>{esc(feedback.REASONS[i.reason])}</span>" if i.reason in feedback.REASONS else ""}
<span class="muted small">{local_time(i.updated_at)}</span>
<a class="small" href="/panel/casos/{i.case_id}">ver caso</a></div>
<div class="msg customer"><div class="who">Cliente</div>{esc(mask_codes(i.question))}</div>
<div class="msg agent"><div class="who">Asistente</div>{esc(mask_codes(i.reply))}</div>
{f'<p class="muted">“{esc(i.comment)}”</p>' if i.comment else ""}{_suggestion(i)}
<form method="post" action="/panel/mejorar/{i.id}/eval">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<label for="esperada-{i.id}">Respuesta esperada</label>
<textarea id="esperada-{i.id}" name="respuesta" maxlength="{feedback.MAX_EXPECTED}"
 required></textarea>
<label for="palabras-{i.id}">Palabras clave que tiene que incluir (opcional, separadas por
coma)</label><input id="palabras-{i.id}" name="palabras" maxlength="300">
<p><button class="primary" type="submit">Convertir en caso de eval</button></p></form></div>"""
            for i in items
        )
        or '<div class="card empty">No hay respuestas marcadas como incorrectas.</div>'
    )
    body = f"""{toast(request.query_params.get("aviso"))}<h1>Para mejorar</h1>
<p class="lead">Las respuestas que el equipo marcó como incorrectas. Escribí la respuesta esperada
y convertila en un caso de eval: el reporte de preparación la prueba junto con los demás.</p>
{cards}"""
    return HTMLResponse(page("Para mejorar", body, active="test/improve", session=ctx.session))


@router.post("/mejorar/{review_id}/eval")
def create_eval(review_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        user_id = _user_id(conn, ctx.tenant_id, ctx.session.email)
        try:
            feedback.to_eval(
                conn,
                ctx.tenant_id,
                review_id,
                expected=form.get("respuesta", ""),
                keywords=form.get("palabras", ""),
                created_by=user_id,
            )
        except feedback.ReviewError:
            return RedirectResponse("/panel/test/improve", status_code=303)
    return RedirectResponse("/panel/test/improve?aviso=eval-creado", status_code=303)
