"""Knowledge (Hito 13, tarea 6; decisión 088): lo que el agente usa para responder.

- Subir arrastrando (PDF, DOCX, TXT, MD, CSV y exportes de chats y tickets), con el estado de
  cada archivo: procesando, listo, o con error y qué hacer.
- El sitio de la empresa: las páginas que encontró, cuáles usar y la sincronización.
- Qué aprendió de cada documento, reemplazarlo y borrarlo.
- "Ask your knowledge" y los problemas para arreglar.
- Aprender del historial (el análisis del onboarding, decisión 026).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.companies import knowledge, onboarding
from app.companies.onboarding import OnboardingError
from app.onboarding import ingest as ingestion
from app.onboarding.ingest import Upload
from app.panel import english, spanish
from app.panel.company_routes import (
    STAGE_STATUS,
    Files,
    _back,
    _csrf,
    _not_found,
    _notice,
    _pending_html,
    head,
)
from app.panel.html import esc, local_time, page
from app.panel.routes import Ctx, Deps, Form, PanelDeps, enter

router = APIRouter(prefix="/panel", include_in_schema=False)
ACCEPT = ".pdf,.docx,.txt,.md,.csv,.json,.zip"

KNOWLEDGE_CSS = """<style>
.drop{border:2px dashed var(--line-strong);border-radius:12px;padding:26px;text-align:center;
background:var(--sunken);transition:border-color .15s,background .15s;cursor:pointer}
.drop.over{border-color:var(--brand);background:var(--brand-soft)}
.drop b{display:block;font-size:15px;margin-bottom:4px}
.js-on #archivos{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden}
.uploads{list-style:none;padding:0;margin:12px 0 0}.uploads li{display:flex;gap:10px;
align-items:flex-start;padding:8px 0;border-top:1px solid var(--line)}
.uploads .msg2{color:var(--muted);font-size:13px}
.answer{border-left:3px solid var(--brand);padding:10px 14px;background:var(--brand-soft);
border-radius:8px;margin:12px 0}.quote{border-left:3px solid var(--line-strong);padding:6px 12px;
margin:8px 0;color:var(--ink-2);font-size:13.5px;white-space:pre-wrap}
.pages{max-height:320px;overflow:auto;border:1px solid var(--line);border-radius:8px;
padding:4px 10px}
.pages label{display:flex;gap:8px;font-weight:400;margin:0;padding:6px 0;
border-top:1px solid var(--line)}.pages label:first-child{border-top:0}
.problem{padding:10px 0;border-top:1px solid var(--line)}.problem:first-of-type{border-top:0}
.section-view{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.section-view pre{background:none;padding:0;margin:6px 0 0;white-space:pre-wrap;font:inherit;
font-size:13.5px}
</style>"""

# Sube los archivos de a uno y muestra el estado de cada uno mientras se procesan.
UPLOAD_JS = """(function(){document.documentElement.classList.add("js-on");
var zone=document.getElementById("drop"),input=document.getElementById(
"archivos"),list=document.getElementById("uploads");if(!zone||!input||!window.fetch)return;
var csrf=document.querySelector('#upload-form input[name=csrf]').value;var T=JSON.parse(
document.getElementById("upload-texts").textContent);
function row(name){var li=document.createElement("li");
li.innerHTML='<span class="badge processing">'
+T.processing+'</span><div class="grow"><b></b><div class="msg2"></div></div>';
li.querySelector("b").textContent=name;list.appendChild(li);return li}
function done(li,r){var b=li.querySelector(".badge");b.className="badge "+r.status;b.textContent=
T[r.status]||r.status;li.querySelector(".msg2").textContent=r.message||""}
function send(files){var queue=Array.prototype.slice.call(files);var any=false;(function next(){
var f=queue.shift();if(!f){if(any)setTimeout(function(){location.reload()},1500);return}
var li=row(f.name);var data=new FormData();data.append("csrf",csrf);data.append("archivos",f);
fetch("/panel/knowledge/upload",{method:"POST",body:data,headers:{Accept:"application/json"},
credentials:"same-origin"}).then(function(r){return r.json()}).then(function(r){
(r.files||[]).forEach(function(x){done(li,x);if(x.status!=="failed")any=true});next()},
function(){done(li,{status:"failed",message:T.network});next()})})()}
zone.addEventListener("click",function(){input.click()});zone.addEventListener("keydown",
function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();input.click()}});
["dragenter","dragover"].forEach(function(n){zone.addEventListener(n,function(e){
e.preventDefault();zone.classList.add("over")})});["dragleave","drop"].forEach(function(n){
zone.addEventListener(n,function(e){e.preventDefault();zone.classList.remove("over")})});
zone.addEventListener("drop",function(e){send(e.dataTransfer.files)});
input.addEventListener("change",function(){send(input.files);input.value=""});
var form=document.getElementById("upload-form");if(form)form.addEventListener("submit",
function(e){e.preventDefault();send(input.files)})})();"""

STATUS = {
    "ready": ("ok", "Ready"),
    "failed": ("bad", "Failed"),
    "waiting": ("warn", "Needs you"),
    "skipped": ("", "Already uploaded"),
    "processing": ("processing", "Processing…"),
}


def _t(text: str) -> str:
    """Un texto para el navegador (lo que se arma con JavaScript no pasa por el traductor)."""
    if english.english():
        return english.translate_text(text)
    return spanish.translate_text(text)


def _upload_texts() -> str:
    import json

    texts = {key: _t(label) for key, (_, label) in STATUS.items()}
    texts["network"] = _t("The upload failed. Check your connection and try again.")
    return f'<script type="application/json" id="upload-texts">{esc(json.dumps(texts))}</script>'


# -- la pantalla ---------------------------------------------------------------------------------
def _upload_card(ctx: Ctx, names: list[str]) -> str:
    return f"""<section class="card" aria-labelledby="add-title">
<h2 id="add-title" style="margin-top:0">Add documents</h2>
<p class="help">Policies, FAQs, product lists and your support history. Your agent answers only
with what's here.</p>
<form method="post" action="/panel/onboarding/fuentes" enctype="multipart/form-data"
 id="upload-form">{_csrf(ctx)}
<div class="drop" id="drop" role="button" tabindex="0" aria-describedby="drop-help">
<b>Drag your files here, or click to choose</b>
<span id="drop-help" class="muted small">PDF, DOCX, TXT, MD and CSV (a catalog or FAQs),
WhatsApp chat exports (.txt or .zip) and helpdesk tickets (.csv or .json). Up to 50 MB.</span>
</div>
<input id="archivos" name="archivos" type="file" multiple accept="{ACCEPT}"
 aria-label="Choose files" style="margin-top:10px">
<noscript><p><button class="primary" type="submit">Upload</button></p></noscript></form>
<ul class="uploads" id="uploads" aria-live="polite"></ul>{_upload_texts()}
<details style="margin-top:10px"><summary>Uploading WhatsApp chats?</summary>
<form method="post" action="/panel/onboarding/nombres">{_csrf(ctx)}
<label for="nombres">The name your business uses in those chats</label>
<input id="nombres" name="nombres" value="{esc(", ".join(names))}"
 placeholder="Acme Support, Ana from Acme">
<p class="help">So we know which messages are from your team. Separate names with commas, and
save them before uploading the chats. Personal data in chats is replaced before saving.</p>
<button type="submit">Save names</button></form></details></section>"""


def _site_card(ctx: Ctx, found: knowledge.Site | None, pages: list[knowledge.Page]) -> str:
    if found is None:
        return f"""<section class="card" aria-labelledby="site-title">
<h2 id="site-title" style="margin-top:0">Your website</h2>
<p class="help">We read your site's pages (up to 30, without leaving your domain) and your agent
learns from them.</p>
<form method="post" action="/panel/knowledge/site" class="row" style="align-items:flex-end">
{_csrf(ctx)}<div class="grow"><label for="sitio">Website address</label>
<input id="sitio" name="sitio" type="url" required placeholder="https://www.yourstore.com"></div>
<button class="primary" type="submit">Find pages</button></form></section>"""
    when = local_time(found.last_sync_at, "%d/%m/%Y %H:%M") if found.last_sync_at else "never"
    chosen = sum(p.selected for p in pages)
    error = (
        f'<div class="note" role="alert">The last sync failed: {esc(found.last_error)}</div>'
        if found.last_error
        else ""
    )
    boxes = "".join(
        f'<label><input type="checkbox" name="pagina" value="{p.id}"'
        f'{" checked" if p.selected else ""}><span class="grow"><b>{esc(p.title)}</b><br>'
        f'<span class="mono muted">{esc(p.url)}</span></span></label>'
        for p in pages
    )
    return f"""<section class="card" aria-labelledby="site-title">
<div class="row"><h2 id="site-title" class="grow" style="margin:0">Your website</h2>
<span class="muted small"><span>Last sync:</span> {when}</span></div>
<p class="mono small">{esc(found.url)}</p>{error}
<form method="post" action="/panel/knowledge/site/pages">{_csrf(ctx)}
<p class="small"><b>{chosen} of {len(pages)}</b> <span>pages in use. Uncheck the ones your agent
shouldn't use.</span></p><div class="pages">{boxes}</div>
<p><button type="submit">Use selected pages</button></p></form>
<div class="row"><form method="post" action="/panel/knowledge/site/sync">{_csrf(ctx)}
<button class="primary" type="submit">Sync now</button></form>
<form method="post" action="/panel/knowledge/site/auto">{_csrf(ctx)}
<input type="hidden" name="activo" value="{"no" if found.auto_sync else "si"}">
<button type="submit">{"Stop weekly sync" if found.auto_sync else "Sync every week"}</button>
</form><span class="muted small">{
        "Syncs automatically every week." if found.auto_sync else "Syncs only when you ask."
    }</span></div></section>"""


def _ask_card(ctx: Ctx, answer: knowledge.Answer | None) -> str:
    result = ""
    if answer is not None:
        quotes = "".join(
            f"""<div translate="no"><a href="/panel/knowledge/doc/{r.document_id}">
<b>{esc(r.title)}</b></a>
<div class="quote">{esc(_snippet(r.text))}</div></div>"""
            for r in answer.sources
        )
        if answer.generated and answer.text:
            body = f'<div class="answer" role="status" translate="no">{esc(answer.text)}</div>'
        elif answer.sources:
            body = '<p class="small muted">Here\'s what your knowledge says about it:</p>'
        else:
            body = (
                '<div class="note" role="status">Your knowledge doesn\'t answer this yet. '
                "Your agent would pass it to a person. Add a document that covers it.</div>"
            )
        sources = f"<h3>Sources it used</h3>{quotes}" if quotes else ""
        result = f'<div style="margin-top:12px">{body}{sources}</div>'
    value = esc(answer.question) if answer else ""
    return f"""<section class="card" aria-labelledby="ask-title">
<h2 id="ask-title" style="margin-top:0">Ask your knowledge</h2>
<p class="help">Check what your agent would answer and which documents it would use, without
opening the chat.</p>
<form method="post" action="/panel/knowledge/ask" class="row" style="align-items:flex-end">
{_csrf(ctx)}<div class="grow"><label for="pregunta" class="small">Question</label>
<input id="pregunta" name="pregunta" required maxlength="500" value="{value}"
 placeholder="How long do I have to return a pair of shoes?"></div>
<button class="primary" type="submit">Ask</button></form>{result}</section>"""


def _snippet(text: str) -> str:
    lines = text.split("\n")
    body = "\n".join(line for line in lines[1:] if line.strip()) if len(lines) > 1 else text
    return body[:600] + ("…" if len(body) > 600 else "")


TOPIC_EN = {key: names[1] for key, names in knowledge.TOPICS.items()}


def _problems_card(found: knowledge.Problems) -> str:
    if not found.count:
        return """<section class="card" aria-labelledby="problems-title">
<h2 id="problems-title" style="margin-top:0">Problems to fix</h2>
<div class="empty small"><b>No problems found</b>We look for documents that contradict each
other, questions your agent couldn't answer and information that looks old.</div></section>"""
    items = []
    for conflict in found.conflicts:
        values = "".join(
            f"<li><b>{esc(v)}</b> <span>in</span> {esc(title)}</li>" for v, title in conflict.values
        )
        items.append(
            f"""<div class="problem"><span class="badge bad">Contradiction</span>
<b>{TOPIC_EN[conflict.topic]}</b><ul class="small">{values}</ul>
<p class="help">Your agent could give different answers. Fix the document that's wrong, or
remove it.</p></div>"""
        )
    for topic, count in found.gaps:
        items.append(
            f"""<div class="problem"><span class="badge warn">Not covered</span>
<b>“{esc(topic)}”</b> <span class="muted small">· <span>{count}</span>
<span>questions your agent couldn't answer</span></span>
<p class="help">Add a document that explains it.</p></div>"""
        )
    for doc, why in found.stale:
        items.append(
            f"""<div class="problem"><span class="badge warn">May be old</span>
<a href="/panel/knowledge/doc/{doc.id}"><b>{esc(doc.title)}</b></a>
<span class="muted small">· {esc(why)}</span>
<p class="help">Check it's still right, and replace it if it isn't.</p></div>"""
        )
    return f"""<section class="card" aria-labelledby="problems-title">
<div class="row"><h2 id="problems-title" class="grow" style="margin:0">Problems to fix</h2>
<span class="badge warn">{found.count}</span></div>{"".join(items)}</section>"""


def _documents_card(ctx: Ctx, docs: list[knowledge.Doc]) -> str:
    files = [d for d in docs if d.site_id is None]
    if not files:
        return """<section class="card" aria-labelledby="docs-title">
<h2 id="docs-title" style="margin-top:0">Your documents</h2><div class="empty small">
<b>No documents yet</b>Upload your policies and FAQs above. It's the first step to go live.
</div></section>"""
    rows = []
    for d in files:
        css, label = STATUS["ready" if d.status == "ready" else "failed"]
        state = f'<span class="badge {css}">{label}</span>'
        detail = (
            f'<span class="muted small"><span>{d.sections}</span> <span>sections</span></span>'
            if d.status == "ready"
            else f'<div class="small" style="color:var(--bad)">{esc(d.error)}</div>'
        )
        replace = (
            f"""<form method="post" action="/panel/knowledge/upload" enctype="multipart/form-data"
 class="replace">{_csrf(ctx)}<input type="hidden" name="reemplazar" value="{d.id}">
<label class="btn" style="margin:0;font-weight:550">Replace<input type="file" name="archivos"
 accept="{ACCEPT}" hidden onchange="this.form.submit()"></label></form>"""
            if d.status == "ready"
            else ""
        )
        rows.append(
            f"""<tr><td translate="no"><a href="/panel/knowledge/doc/{d.id}">
<b>{esc(d.title)}</b></a>
<div class="mono muted">{esc(d.file_name or d.source)}</div></td><td>{state}<br>{detail}</td>
<td class="small muted">{local_time(d.updated_at)}</td><td><div class="row" style="gap:6px">
{replace}<form method="post" action="/panel/onboarding/indexados/{d.id}/borrar"
 data-confirm="Remove this document? Your agent stops using it right away.">{_csrf(ctx)}
<button class="danger" type="submit">Remove</button></form></div></td></tr>"""
        )
    return f"""<section class="card" aria-labelledby="docs-title">
<h2 id="docs-title" style="margin-top:0">Your documents</h2>
<p class="help">Replacing a document updates what your agent knows right away; removing it takes
it out of every answer.</p><div class="table"><table><thead><tr><th>Document</th><th>Status</th>
<th>Updated</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>"""


def _history_card(ctx: Ctx, deps: PanelDeps, found: Any, run: Any) -> str:
    """Aprender del historial: el análisis del onboarding (decisión 026)."""
    source_rows = "".join(
        f"<tr><td>{esc(name)}</td><td class='num'>{count or '—'}</td><td>{local_time(at)}</td></tr>"
        for name, kind, count, at in found
        if kind in ("whatsapp_export", "helpdesk_export")
    )
    steps = "".join(
        f'<li><span class="badge {esc(status)}">{STAGE_STATUS.get(status, status)}</span>'
        f'<span class="grow"><b>{position}. {esc(label)}</b>'
        f'<div class="muted small">{esc(detail)}</div></span></li>'
        for position, (_, label, status, detail) in enumerate(onboarding.progress(run), start=1)
    )
    running = run is not None and run.queued
    if running:
        action = (
            '<p class="muted">The analysis is running. This page updates by itself; you can '
            "close it and come back later.</p>"
        )
    else:
        label = (
            "Analyze my history"
            if run is None
            else "Retry from where it stopped"
            if run.status != "done"
            else "Analyze again (with new files)"
        )
        action = (
            f'<form method="post" action="/panel/onboarding/iniciar">{_csrf(ctx)}'
            f'<button class="primary" type="submit">{label}</button></form>'
        )
    table = (
        "<table><thead><tr><th>File</th><th class=num>Conversations</th><th>Uploaded</th></tr>"
        f"</thead><tbody>{source_rows}</tbody></table>"
        if source_rows
        else '<p class="muted small">Upload WhatsApp chats or helpdesk tickets above to start.</p>'
    )
    finished = (
        """<div class="note" role="status"><b>The analysis finished.</b> See how much your agent
would solve in the <a href="/panel/test">readiness report</a>, then review and approve what it
found: <a href="/panel/agent/topics">topics</a> and <a href="/panel/agent/actions">actions</a>.
Nothing is used until you approve it.</div>"""
        if run is not None and run.status == "done" and not running
        else ""
    )
    return f"""<section class="card" aria-labelledby="history-title">{finished}
<h2 id="history-title" style="margin-top:0">Learn from your support history</h2>
<p class="help">From your past conversations, your agent learns the topics your customers ask
about, builds its tests and tells you how much it would solve on its own.</p>{table}
<details{" open" if run is not None else ""}><summary>Analysis steps</summary>
<ol class="steps">{steps}</ol></details><div style="margin-top:12px">{action}</div></section>"""


def _render(
    request: Request, ctx: Ctx, deps: PanelDeps, answer: knowledge.Answer | None = None
) -> HTMLResponse:
    with deps.connection() as conn:
        docs = knowledge.documents(conn, ctx.tenant_id)
        found_site = knowledge.site(conn, ctx.tenant_id)
        site_pages = knowledge.pages(conn, ctx.tenant_id, found_site.id) if found_site else []
        issues = knowledge.problems(conn, ctx.tenant_id)
        names = sorted(onboarding.business_names(conn, ctx.tenant_id) - {ctx.session.company})
        sources = onboarding.sources(conn, ctx.tenant_id)
        run = onboarding.state(conn, ctx.tenant_id)
        waiting = ingestion.pending_documents(conn, ctx.tenant_id)
    ready = sum(d.status == "ready" and d.site_id is None for d in docs)
    pages_in_use = sum(d.site_id is not None for d in docs)
    failed = sum(d.status == "failed" for d in docs)
    stats = (
        f'<p class="muted small" style="margin-top:-8px"><span>{ready}</span> '
        f"<span>documents ready</span> · <span>{pages_in_use}</span> <span>website pages</span>"
        + (f" · <span>{failed}</span> <span>failed</span>" if failed else "")
        + "</p>"
    )
    lead = "What your agent knows. It answers only with this."
    body = f"""{KNOWLEDGE_CSS}{head("Knowledge", lead)}
{stats}{_notice(request)}{_pending_html(ctx, waiting)}
<div class="grid2" style="align-items:start"><div>{_upload_card(ctx, names)}
{_documents_card(ctx, docs)}</div><div>{_ask_card(ctx, answer)}{_problems_card(issues)}
{_site_card(ctx, found_site, site_pages)}</div></div>
{_history_card(ctx, deps, sources, run)}<script>{UPLOAD_JS}</script>"""
    running = run is not None and run.queued
    return HTMLResponse(
        page(
            "Knowledge",
            body,
            active="knowledge",
            session=ctx.session,
            refresh=8 if running else None,
        )
    )


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    return _render(request, ctx, deps)


# -- subir -----------------------------------------------------------------------------------------
def _one(deps: PanelDeps, tenant_id: uuid.UUID, upload: Upload) -> dict[str, Any]:
    """Procesa un archivo y dice cómo quedó: listo, falló (y por qué), espera a la empresa o ya
    estaba."""
    with deps.connection() as conn:
        try:
            result = onboarding.add_sources(conn, tenant_id, [upload], deps.embedder(conn))
        except OnboardingError as error:
            knowledge.record_failure(conn, tenant_id, upload.name, str(error))
            return {"name": upload.name, "status": "failed", "message": _t(str(error))}
        if result.unreadable:
            reason = " ".join(result.unreadable)
            knowledge.record_failure(conn, tenant_id, upload.name, reason)
            return {"name": upload.name, "status": "failed", "message": _t(reason)}
        if result.held:
            return {
                "name": upload.name,
                "status": "waiting",
                "message": _t("It seems to have people's personal data. Review it on this page."),
            }
        if result.skipped:
            return {"name": upload.name, "status": "skipped", "message": ""}
        if result.conversations:
            message = f"{result.conversations} conversations to learn from"
            return {"name": upload.name, "status": "ready", "message": _t(message)}
        knowledge.mark_uploaded(conn, tenant_id, upload.name)
        return {"name": upload.name, "status": "ready", "message": ""}


@router.post("/knowledge/upload")
async def upload(request: Request, data: Files, deps: Deps) -> Response:
    wants_json = "application/json" in request.headers.get("accept", "")
    if data.too_big:
        message = "The upload is over 50 MB."
        if wants_json:
            return JSONResponse({"files": [{"name": "", "status": "failed", "message": message}]})
        return _back("knowledge", error=message)
    ctx = enter(request, deps, data.fields)
    if isinstance(ctx, Response):
        return ctx
    if not data.files:
        return _back("knowledge", error="Elegí al menos un archivo.")
    files = list(data.files)
    replace = data.fields.get("reemplazar", "")
    if replace:
        # Reemplazar: el archivo nuevo toma el lugar del anterior aunque tenga otro nombre.
        try:
            document_id = uuid.UUID(replace)
        except ValueError:
            return _not_found("Ese documento", ctx.session)

        def source() -> str:
            with deps.connection() as conn:
                return knowledge.document_source(conn, ctx.tenant_id, document_id)

        key = await run_in_threadpool(source)
        if not key:
            return _not_found("Ese documento", ctx.session)
        suffix = "." + files[0].name.rsplit(".", 1)[-1] if "." in files[0].name else ""
        files = [Upload(f"{key}{suffix}", files[0].data)]
    results = [await run_in_threadpool(_one, deps, ctx.tenant_id, f) for f in files]
    if wants_json:
        return JSONResponse({"files": results})
    failed = [r["message"] for r in results if r["status"] == "failed"]
    if failed:
        return _back("knowledge", error="\n".join(failed))
    return _back("knowledge", ok="Documents uploaded.")


# -- el detalle de un documento -------------------------------------------------------------------
@router.get("/knowledge/doc/{document_id}", response_class=HTMLResponse)
def document_page(document_id: uuid.UUID, request: Request, deps: Deps) -> Response:
    ctx = enter(request, deps)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = knowledge.detail(conn, ctx.tenant_id, document_id)
    if found is None:
        return _not_found("Ese documento", ctx.session)
    doc, text, sections = found
    if doc.status != "ready":
        content = f'<div class="card alert" role="alert">{esc(doc.error)}</div>'
    else:
        views = "".join(
            f"""<div class="section-view" translate="no"><b>{esc(s.heading) or "—"}</b>
<pre>{esc(s.text)}</pre></div>"""
            for s in sections
        )
        content = f"""<div class="card"><h2 style="margin-top:0">What your agent learned</h2>
<p class="help">Your agent searches these sections when it answers. Each one carries its title so
it makes sense on its own.</p>{views}</div>
<div class="card"><details><summary>Full extracted text</summary>
<pre style="white-space:pre-wrap" translate="no">{esc(text)}</pre></details></div>"""
    origin = esc(doc.file_name or doc.source)
    body = f"""{KNOWLEDGE_CSS}<p><a href="/panel/knowledge">← Knowledge</a></p>
{head(doc.title, "")}<p class="muted small"><span class="mono">{origin}</span> ·
<span>{doc.sections}</span> <span>sections</span> · <span>{doc.chars}</span>
<span>characters</span> · {local_time(doc.updated_at)}</p>{content}"""
    return HTMLResponse(page(doc.title, body, active="knowledge", session=ctx.session))


# -- preguntar -----------------------------------------------------------------------------------
@router.post("/knowledge/ask", response_class=HTMLResponse)
async def ask(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    question = form.get("pregunta", "")

    def run() -> knowledge.Answer:
        with deps.connection() as conn:
            return knowledge.ask(
                conn,
                ctx.tenant_id,
                deps.embedder(conn),
                deps.team_llm(conn, ctx.tenant_id),
                question,
            )

    answer = await run_in_threadpool(run)
    return _render(request, ctx, deps, answer)


# -- el sitio ------------------------------------------------------------------------------------
async def _sync(request: Request, deps: Deps, ctx: Ctx, url: str) -> Response:
    def run() -> int:
        with deps.connection() as conn:
            knowledge.sync(conn, ctx.tenant_id, url, deps.embedder(conn))
            found = knowledge.site(conn, ctx.tenant_id)
            return len(knowledge.pages(conn, ctx.tenant_id, found.id)) if found else 0

    try:
        count = await run_in_threadpool(run)
    except Exception as error:  # noqa: BLE001 — se informa el motivo
        return _back("knowledge", error=f"We couldn't read the site: {error}")
    return _back("knowledge", ok=f"Website synced: {count} pages found.")


@router.post("/knowledge/site")
async def add_site(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    url = form.get("sitio", "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return _back("knowledge", error="La dirección tiene que empezar con https://.")
    return await _sync(request, deps, ctx, url)


@router.post("/knowledge/site/sync")
async def sync_site(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = knowledge.site(conn, ctx.tenant_id)
    if found is None:
        return _back("knowledge")
    return await _sync(request, deps, ctx, found.url)


@router.post("/knowledge/site/pages")
async def choose_pages(request: Request, deps: Deps) -> Response:
    from urllib.parse import parse_qs

    body = (await request.body()).decode("utf-8", errors="replace")
    fields = parse_qs(body, keep_blank_values=True)
    form = {k: v[0] for k, v in fields.items()}
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    chosen = set()
    for value in fields.get("pagina", []):
        try:
            chosen.add(uuid.UUID(value))
        except ValueError:
            continue

    def run() -> None:
        with deps.connection() as conn:
            found = knowledge.site(conn, ctx.tenant_id)
            if found is not None:
                knowledge.choose(conn, ctx.tenant_id, found.id, chosen, deps.embedder(conn))

    await run_in_threadpool(run)
    return _back("knowledge", ok="Pages saved.")


@router.post("/knowledge/site/auto")
def site_auto(request: Request, form: Form, deps: Deps) -> Response:
    ctx = enter(request, deps, form)
    if isinstance(ctx, Response):
        return ctx
    with deps.connection() as conn:
        found = knowledge.site(conn, ctx.tenant_id)
        if found is not None:
            knowledge.set_auto(conn, ctx.tenant_id, found.id, form.get("activo") == "si")
    return _back("knowledge", ok="Saved.")
