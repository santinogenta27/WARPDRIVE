"""Memoria de los clientes en el panel (Hito 12, decisión 078).

- `GET /panel/clientes`: las memorias de la empresa y su retención.
- `GET /panel/clientes/{id}`: una memoria, con el formulario para corregirla y para borrarla.
- `POST /panel/clientes/{id}`: corrige el nombre y las preferencias.
- `POST /panel/clientes/{id}/borrar`: borra la memoria entera del cliente.
- `POST /panel/clientes/retencion`: cuántos meses se guarda (solo admins).

La clave del cliente (el hash del teléfono) nunca se muestra. Cada cambio queda en la auditoría.
"""

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import memory
from app.audit import log as audit_log
from app.panel import english
from app.panel.html import esc, local_time, page, toast
from app.panel.labels import type_label
from app.panel.routes import Ctx, Deps, Form, _forbidden, _not_found, enter

router = APIRouter(prefix="/panel", include_in_schema=False)


def _can_configure(ctx: Ctx) -> bool:
    return ctx.session.admin or ctx.session.role != "agent"


def _lang() -> str:
    return "en" if english.english() else "es"


def _who(ctx: Ctx) -> str:
    return ctx.session.email


@router.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = memory.list_all(conn, ctx.tenant_id)
        months = memory.retention_months(conn, ctx.tenant_id)
    unnamed = "<span>Sin nombre</span>"
    rows = "".join(
        f"""<tr><td><a href="/panel/clientes/{m.id}">{esc(m.name) or unnamed}</a></td>
<td>{len(m.purchases)}</td><td>{len(m.history)}</td><td>{local_time(m.last_seen_at)}</td></tr>"""
        for m in found
    )
    table = (
        f"""<table><thead><tr><th>Cliente</th><th>Compras</th><th>Conversaciones</th>
<th>Última vez</th></tr></thead><tbody>{rows}</tbody></table>"""
        if found
        else '<div class="card empty">Todavía no hay clientes verificados con memoria.</div>'
    )
    retention = (
        f"""<form method="post" action="/panel/clientes/retencion" class="row" style="gap:8px">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<label for="meses">Guardar la memoria sin actividad durante</label>
<input id="meses" name="meses" type="number" min="{memory.MIN_RETENTION_MONTHS}"
 max="{memory.MAX_RETENTION_MONTHS}" value="{months}" style="max-width:90px">
<span>meses</span><button type="submit">Guardar</button></form>"""
        if _can_configure(ctx)
        else f"<p><span>Meses de retención:</span> {months}</p>"
    )
    body = f"""{toast(request.query_params.get("aviso"))}<h1>Clientes</h1>
<p class="lead">Lo que el asistente recuerda de cada cliente verificado: nombre, conversaciones
anteriores, preferencias y compras. Solo lo usa después de verificar la identidad y nunca guarda
datos de pago.</p>
<div class="card"><h2>Retención</h2>{retention}</div>
<div class="card">{table}</div>"""
    return HTMLResponse(page("Clientes", body, active="customers", session=ctx.session))


@router.post("/clientes/retencion")
def save_retention(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    if not _can_configure(ctx):
        return _forbidden("Esta acción es solo para los admins de la empresa.")
    try:
        months = int(form.get("meses", ""))
        with deps.connection() as conn:
            memory.set_retention(conn, ctx.tenant_id, months)
            audit_log.record(
                conn,
                ctx.tenant_id,
                None,
                "customer_memory_retention",
                {"meses": months, "por": _who(ctx)},
            )
    except ValueError:
        return RedirectResponse("/panel/customers?aviso=retencion-invalida", status_code=303)
    return RedirectResponse("/panel/customers?aviso=retencion-guardada", status_code=303)


def _section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lines = "".join(f"<li>{item}</li>" for item in items)
    return f"<h2>{esc(title)}</h2><ul>{lines}</ul>"


@router.get("/clientes/{memory_id}", response_class=HTMLResponse)
def customer_page(memory_id: uuid.UUID, request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = memory.get(conn, ctx.tenant_id, memory_id)
    if found is None:
        return _not_found("Ese cliente", ctx.session)
    purchases = [
        f"<span>Pedido</span> {esc(p['pedido'])}: {esc(', '.join(p['productos']))}"
        f" ({esc(p['estado'])}, {esc(p['fecha'])})"
        for p in found.purchases
    ]
    history = [
        f"{esc(h.get('fecha', ''))}: {esc(type_label(h.get('tema') or None))}"
        + (f" · <span>Pedido</span> {esc(h['pedido'])}" if h.get("pedido") else "")
        + f" · <span>{esc(memory.outcome_text(str(h.get('resultado')), _lang()))}</span>"
        + f' · <a href="/panel/casos/{esc(h["caso"])}">ver caso</a>'
        for h in reversed(found.history)
    ]
    title = esc(found.name) or "<span>Sin nombre</span>"
    saved = esc(chr(10).join(found.preferences))
    body = f"""{toast(request.query_params.get("aviso"))}
<p><a href="/panel/customers">← Clientes</a></p><h1>{title}</h1>
<p class="muted"><span>Última vez:</span> {local_time(found.last_seen_at)}</p>
<div class="card">{_section("Compras", purchases) or "<p>Sin compras registradas.</p>"}
{_section("Conversaciones anteriores", history)}</div>
<div class="card"><h2>Corregir</h2>
<form method="post" action="/panel/clientes/{found.id}">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<label for="nombre">Nombre</label>
<input id="nombre" name="nombre" maxlength="{memory.MAX_NAME_CHARS}" value="{esc(found.name)}">
<label for="preferencias">Preferencias (una por línea)</label>
<textarea id="preferencias" name="preferencias" rows="4">{saved}</textarea>
<p class="muted small">Una preferencia con datos de pago no se guarda.</p>
<p><button class="primary" type="submit">Guardar cambios</button></p></form></div>
<div class="card"><h2>Borrar la memoria</h2>
<p>El asistente deja de recordar a este cliente. Sus conversaciones siguen en Casos.</p>
<form method="post" action="/panel/clientes/{found.id}/borrar">
<input type="hidden" name="csrf" value="{ctx.csrf}">
<button class="danger" type="submit">Borrar la memoria de este cliente</button></form></div>"""
    return HTMLResponse(page("Cliente", body, active="customers", session=ctx.session))


@router.post("/clientes/{memory_id}")
def correct_customer(memory_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    preferences = form.get("preferencias", "").splitlines()
    with deps.connection() as conn:
        if not memory.correct(
            conn, ctx.tenant_id, memory_id, name=form.get("nombre", ""), preferences=preferences
        ):
            return _not_found("Ese cliente", ctx.session)
        audit_log.record(
            conn,
            ctx.tenant_id,
            None,
            "customer_memory_updated",
            {"memoria": str(memory_id), "por": _who(ctx)},
        )
    return RedirectResponse(f"/panel/clientes/{memory_id}?aviso=memoria-guardada", status_code=303)


@router.post("/clientes/{memory_id}/borrar")
def forget_customer(memory_id: uuid.UUID, request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        if not memory.forget(conn, ctx.tenant_id, memory_id):
            return _not_found("Ese cliente", ctx.session)
        audit_log.record(
            conn,
            ctx.tenant_id,
            None,
            "customer_memory_deleted",
            {"memoria": str(memory_id), "por": _who(ctx)},
        )
    return RedirectResponse("/panel/customers?aviso=memoria-borrada", status_code=303)
