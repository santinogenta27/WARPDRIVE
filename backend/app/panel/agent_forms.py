"""Agent: configurar sin miedo (Hito 13, tarea 5; decisión 087).

Cada opción lleva una línea de ayuda en lenguaje simple y un ejemplo (`field`). La marca y la voz
comparten una vista previa: el chat real de la empresa, que se actualiza al escribir, con una
respuesta de ejemplo armada con la voz (tono, trato, emojis y firma). Los formularios envían a
los mismos endpoints de antes (`/panel/configuracion/...`) y al guardar aparece "Saved".
"""

from typing import Any

from app.agent import voice as brand_voice
from app.channels.web import widget
from app.companies import rules
from app.panel.html import esc
from app.panel.labels import type_label
from app.sales import upsell


def field(label: str, control: str, help_text: str, example: str = "", target: str = "") -> str:
    """Una opción: su nombre, el control, qué hace en una línea y un ejemplo."""
    attr = f' for="{target}"' if target else ""
    sample = (
        f'<p class="example"><span>Example:</span> <span>{esc(example)}</span></p>'
        if example
        else ""
    )
    return f"""<div class="field"><label{attr}>{label}</label>{control}
<p class="help">{help_text}</p>{sample}</div>"""


def toggle(name: str, action: str, on: bool, csrf: str, help_text: str, example: str) -> str:
    """Un interruptor: estado, qué hace, un ejemplo y el botón para cambiarlo."""
    state = "On" if on else "Off"
    return f"""<div class="field toggle"><div class="row" style="align-items:flex-start">
<div class="grow"><label>{name}</label><p class="help">{help_text}</p>
<p class="example"><span>Example:</span> <span>{esc(example)}</span></p></div>
<span class="badge {"ok" if on else ""}">{state}</span>
<form method="post" action="{action}"><input type="hidden" name="csrf" value="{csrf}">
<button name="activo" value="{"no" if on else "si"}" class="{"" if on else "primary"}">
{"Turn off" if on else "Turn on"}</button></form></div></div>"""


FORM_CSS = """<style>
.field{padding:14px 0;border-top:1px solid var(--line)}.card>form>.field:first-of-type,
.card>.field:first-child{border-top:0;padding-top:0}
.field>label{margin-top:0}.field .help{margin-top:6px}.field input:not([type=checkbox]):not(
[type=radio]):not([type=color]),.field select,.field textarea{max-width:520px}
.field .example span:first-child{font-weight:600}
.days{display:flex;gap:6px;flex-wrap:wrap}.days label{font-weight:400;margin:0;display:flex;
gap:6px;align-items:center;padding:6px 10px;border:1px solid var(--line);border-radius:8px}
.formbar{display:flex;gap:10px;align-items:center;padding-top:14px;
border-top:1px solid var(--line)}
.split{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:16px;align-items:start}
.split .preview{position:sticky;top:16px}
@media (max-width:1100px){.split{grid-template-columns:1fr}.split .preview{position:static}}
</style>"""

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def rules_html(csrf: str, found: rules.Rules) -> str:
    hours = found.hours
    days = "".join(
        f'<label><input type="checkbox" name="dia_{n}" value="si"'
        f"{' checked' if hours and n in hours.days else ''}> {label}</label>"
        for n, label in enumerate(DAYS, start=1)
    )
    start = esc(hours.start if hours else "")
    end = esc(hours.end if hours else "")
    team = f"""<div class="days" role="group" aria-label="Days">{days}</div>
<div class="row" style="margin-top:8px"><div><label for="desde" class="small">From</label>
<input id="desde" name="desde" type="time" value="{start}"></div><div>
<label for="hasta" class="small">To</label>
<input id="hasta" name="hasta" type="time" value="{end}"></div></div>"""
    email = (
        f'<input id="derivar_a" name="derivar_a" type="email" placeholder="support@yourstore.com"'
        f' value="{esc(found.handoff_email or "")}">'
    )
    return f"""<div class="card"><form method="post" action="/panel/configuracion/reglas">
<input type="hidden" name="csrf" value="{csrf}">
{
        field(
            "Your team's hours",
            team,
            "Your agent answers 24/7. Outside these hours, when it passes a conversation to a "
            "person, it tells the customer when your team will reply.",
            "Mon to Fri, 9:00 to 18:00",
        )
    }
{
        field(
            "Who gets hand-offs and approvals",
            email,
            "This email is notified when a conversation needs a person or an approval. Empty: "
            "every user of your company.",
            "support@yourstore.com",
            "derivar_a",
        )
    }
<div class="formbar"><button class="primary" type="submit">Save</button></div></form></div>"""


def review_html(csrf: str, supervised: bool, checking: bool, stats: dict[str, Any]) -> str:
    seconds = f"{stats['avg_ms'] / 1000:.1f} s"
    table = f"""<table class="small" style="max-width:420px;margin-top:8px"><tbody>
<tr><td>Answers checked (30 days)</td><td class="num">{stats["reviews"]}</td></tr>
<tr><td>Rewritten</td><td class="num">{stats["rewrites"]}</td></tr>
<tr><td>Passed to a person</td><td class="num">{stats["escalations"]}</td></tr>
<tr><td>Extra time per answer</td><td class="num">{seconds}</td></tr>
<tr><td>Cost</td><td class="num">US$ {stats["cost_usd"]:.4f}</td></tr></tbody></table>"""
    return f"""<div class="card">{
        toggle(
            "Review before sending",
            "/panel/configuracion/supervisado",
            supervised,
            csrf,
            "Every answer waits in Inbox → Drafts until someone on your team sends, edits or "
            "rejects it. The customer sees “typing…” meanwhile.",
            "Turn it on for your first days; turn it off when the answers look right.",
        )
    }{
        toggle(
            "Answer check",
            "/panel/configuracion/supervisor",
            checking,
            csrf,
            "A second, low-cost AI checks every answer with facts, policies or actions before "
            "it goes out. If something is off, it asks for one rewrite or passes the "
            "conversation to a person.",
            "It stops an answer that promises a refund your policy doesn't allow.",
        )
    }{table}</div>"""


TREATMENTS = (("", "Based on the language"), ("vos", "Vos"), ("tu", "Tú"), ("usted", "Usted"))


def voice_html(csrf: str, found: brand_voice.Voice, language: str, company: str) -> str:
    options = "".join(
        f'<option value="{code}"{" selected" if found.trato == code else ""}>{esc(label)}</option>'
        for code, label in TREATMENTS
    )
    # El trato es del español: en una empresa en inglés no se muestra.
    treatment = (
        field(
            "Form of address in Spanish",
            f'<select id="trato" name="trato">{options}</select>',
            "How your agent addresses customers when it writes in Spanish.",
            "Vos, for customers in Argentina",
            "trato",
        )
        if language != "en"
        else '<input type="hidden" name="trato" value="">'
    )
    checked = " checked" if found.emojis else ""
    return f"""<div class="card"><form method="post" action="/panel/configuracion/voz"
 id="voice-form"><input type="hidden" name="csrf" value="{csrf}">
{
        field(
            "Tone",
            f'<input id="tono" name="tono" maxlength="{brand_voice.MAX_TONE}"'
            f' value="{esc(found.tono)}" placeholder="Warm and friendly, straight to the point">',
            "A few words about how your agent should sound.",
            "Warm and friendly, straight to the point",
            "tono",
        )
    }{treatment}
{
        field(
            "Words to use",
            f'<input id="usar" name="usar" value="{esc(", ".join(found.usar))}">',
            "Separated by commas. Your agent uses them when they fit.",
            "shipping, exchange, size guide",
            "usar",
        )
    }
{
        field(
            "Words to avoid",
            f'<input id="evitar" name="evitar" value="{esc(", ".join(found.evitar))}">',
            "Separated by commas. An answer that uses one is rewritten before it's sent.",
            "cheap, problem, unfortunately",
            "evitar",
        )
    }
{
        field(
            "Emojis",
            f'<label class="row small" style="gap:8px;font-weight:400;margin:0"><input '
            f'type="checkbox" id="emojis" name="emojis" value="si"{checked}> Use emojis</label>',
            "Off: emojis are removed from every answer before it's sent.",
            "On for a casual brand; off for a formal one",
        )
    }
{
        field(
            "Signature",
            f'<input id="firma" name="firma" maxlength="{brand_voice.MAX_SIGNATURE}"'
            f' value="{esc(found.firma)}" placeholder="— The {esc(company)} team">',
            "Added at the end of every answer. Empty: no signature.",
            f"— The {company} team",
            "firma",
        )
    }
<div class="formbar"><button class="primary" type="submit">Save</button></div></form></div>"""


def brand_html(csrf: str, found: widget.WidgetSettings) -> str:
    logo_note = (
        '<label class="row small" style="gap:6px;font-weight:400;margin:6px 0 0"><input '
        'type="checkbox" name="quitar_logo" value="si"> Remove the current logo</label>'
        if found.has_logo
        else ""
    )
    color = f"""<div class="row"><input type="color" name="pick" aria-label="Pick a color"
 value="{esc(found.accent)}" style="width:54px;height:40px;padding:4px">
<input id="color" name="color" class="mono" maxlength="7" required style="max-width:140px"
 pattern="#[0-9a-fA-F]{{6}}" value="{esc(found.accent)}"></div>"""
    return f"""<div class="card"><form method="post" action="/panel/configuracion/widget"
 enctype="multipart/form-data" id="widget-form"><input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="volver" value="agent/voice">
{
        field(
            "Name customers see",
            f'<input id="nombre" name="nombre" maxlength="60" required value="{esc(found.name)}">',
            "Shown at the top of the chat.",
            "Acme Store",
            "nombre",
        )
    }
{
        field(
            "Accent color",
            color,
            "Used for the chat button and your agent's messages.",
            "#4338ca",
            "color",
        )
    }
{
        field(
            "Logo",
            '<input id="logo" name="logo" type="file" accept="image/png,image/jpeg,image/webp">'
            + logo_note,
            "A square PNG, JPG or WebP image, up to 200 KB.",
            "Your store's icon",
            "logo",
        )
    }
{
        field(
            "Your agent's name",
            f'<input id="asistente" name="asistente" maxlength="40"'
            f' value="{esc(found.assistant)}" placeholder="Nina">',
            "Optional. How your agent introduces itself.",
            "Nina",
            "asistente",
        )
    }
{
        field(
            "Greeting",
            f'<textarea id="saludo" name="saludo" maxlength="300" style="min-height:70px"'
            f' placeholder="{esc(widget.greeting(found.name, found.language))}">'
            f"{esc(found.greeting)}</textarea>",
            "The first message customers see when they open the chat.",
            "Hi! I'm Nina from Acme. How can I help?",
            "saludo",
        )
    }
{
        field(
            "Suggested questions",
            f'<textarea id="sugerencias" name="sugerencias" style="min-height:90px">'
            f"{esc(chr(10).join(found.suggestions))}</textarea>",
            "Up to 4, one per line. Customers can tap them when the chat is empty.",
            "Where is my order?",
            "sugerencias",
        )
    }
<div class="formbar"><button class="primary" type="submit">Save</button></div></form></div>"""


# Vista previa compartida de marca y voz: cada cambio se ve en el chat antes de guardar.
PREVIEW_JS = """(function(){var w=document.getElementById("widget-form"),v=document.getElementById(
"voice-form");var logo=null;function val(f,n){var e=f&&f.elements[n];return e?e.value:""}
var es=document.getElementById("brand-preview").getAttribute("data-lang")==="es";
function sample(){if(!v)return"";var t=val(v,"trato");
var base=es?(t==="usted"
?"¡Hola! Su pedido #1001 ya está en camino y llega el jueves. ¿Necesita algo más?"
:t==="tu"?"¡Hola! Tu pedido #1001 ya está en camino y llega el jueves. ¿Necesitas algo más?"
:"¡Hola! Tu pedido #1001 ya está en camino y llega el jueves. ¿Necesitás algo más?")
:"Hi! Your order #1001 is on its way and arrives on Thursday. Anything else I can help with?";
if(v.elements.emojis&&v.elements.emojis.checked)base+=" 📦";var s=val(v,"firma").trim();
return s?base+"\\n"+s:base}
function push(){if(!window.EnterpriseChat)return;var c={sample:sample()};if(w){
c.name=val(w,"nombre").trim();c.accent=val(w,"color");c.greeting=val(w,"saludo").trim()||w.elements.saludo.placeholder;
c.suggestions=val(w,"sugerencias").split("\\n").map(function(x){return x.trim()})
.filter(Boolean).slice(0,4);if(logo!==null)c.logo=logo}window.EnterpriseChat.setBrand(c)}
[w,v].forEach(function(f){if(f){f.addEventListener("input",push);f.addEventListener("change",push)}});
if(w){w.elements.pick.addEventListener("input",function(){w.elements.color.value=this.value;push()});
w.elements.logo.addEventListener("change",function(){var file=this.files[0];if(!file)return;
var r=new FileReader();r.onload=function(){logo=r.result;push()};r.readAsDataURL(file)})}
var tries=0;(function wait(){if(window.EnterpriseChat){push();return}
if(tries++<40)setTimeout(wait,150)})()
})();"""


def preview_html(slug: str, language: str, company: str) -> str:
    return f"""<div class="card preview" id="brand-preview" data-lang="{esc(language)}">
<h2 style="margin-top:0">Preview</h2>
<p class="help">Your real chat. It updates as you type; nothing changes for customers until you
save.</p>
<div id="widget-preview" style="width:100%;max-width:372px;height:540px;margin-top:10px"></div>
<p class="muted small">Messages you send here show up in your Inbox as {esc(company)}'s chat.</p>
</div><script src="/widget.js" data-chat="{esc(slug)}" data-mode="inline"
 data-target="#widget-preview"></script><script>{PREVIEW_JS}</script>"""


def sales_html(csrf: str, found: list[upsell.Rule], kinds: list[str]) -> str:
    rows = "".join(
        f"<tr><td>{esc(r.product)}</td>"
        f'<td class="data">{esc(", ".join(type_label(k) for k in r.case_types))}</td>'
        f'<td class="num">{r.max_discount:g} %</td><td>'
        f'<form method="post" action="/panel/configuracion/ventas/{r.id}/borrar"'
        ' data-confirm="Stop suggesting this product?">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        '<button type="submit" class="danger">Remove</button></form></td></tr>'
        for r in found
    )
    table = (
        '<div class="table"><table><thead><tr><th>Product</th><th>When</th>'
        f'<th class="num">Max. discount</th><th></th></tr></thead><tbody>{rows}</tbody></table>'
        "</div>"
        if found
        else '<div class="empty small"><b>No products to suggest yet</b>Add one below.</div>'
    )
    boxes = "".join(
        f'<label><input type="checkbox" name="tipo_{esc(k)}" value="si">'
        f' <span class="data">{esc(type_label(k))}</span></label>'
        for k in kinds
    )
    form = (
        f"""<form method="post" action="/panel/configuracion/ventas">
<input type="hidden" name="csrf" value="{csrf}">
{
            field(
                "Product",
                '<input id="producto" name="producto" required>',
                "Use the same name it has in your store, so your agent can find it.",
                "Leather care kit",
                "producto",
            )
        }
{
            field(
                "When to suggest it",
                f'<div class="days" role="group" aria-label="Topics">{boxes}</div>',
                "After solving one of these topics. Never when the customer is upset or "
                "complaining.",
                "After a question about shoe sizes",
            )
        }
{
            field(
                "Maximum discount (%)",
                '<input id="descuento" name="descuento" inputmode="decimal" value="0"'
                ' style="max-width:120px">',
                "Your agent can never offer more than this. 0: no discount.",
                "10",
                "descuento",
            )
        }
<div class="formbar"><button class="primary" type="submit">Add product</button></div></form>"""
        if kinds
        else '<p class="muted small">Turn on a topic first: products are suggested after '
        'solving one. <a href="/panel/agent/topics">Go to Topics</a></p>'
    )
    return f"""<div class="card"><h2 style="margin-top:0">Products to suggest</h2>{table}
<p class="help">At most one product per conversation, only after the customer's question is
solved.</p></div><div class="card"><h2 style="margin-top:0">Add a product</h2>{form}</div>"""


RISK = {"low": "Low risk", "medium": "Medium risk", "high": "High risk"}


def _plain(value: Any) -> str:
    if value is None:
        return ""
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


def actions_html(csrf: str, tools: list[Any]) -> str:
    """Las acciones del agente en la tienda (decisiones 026, 039 y 051): aprobarlas, pedir
    identidad, pedir aprobación y los topes."""
    from app.panel.labels import money, tool_label

    if not tools:
        return (
            '<div class="card empty"><b>No actions yet</b>Your agent\'s actions come from your '
            'store. <a class="btn primary" href="/panel/deploy#store">Connect your store</a></div>'
        )
    hidden = f'<input type="hidden" name="csrf" value="{csrf}">'
    cards = []
    for tool in tools:
        m = tool.manifest
        high = m["risk"] == "high"
        writes = m["upstream"]["method"] != "GET"
        facts = [
            RISK.get(m["risk"], m["risk"]),
            "requires a verified identity"
            if m.get("requires_verified_contact")
            else "no identity needed",
            "changes data" if writes else "read only",
        ]
        if m.get("max_amount") is not None and m.get("amount_from") == "input.percent":
            facts.append(f"cap {m['max_amount']}%")  # un descuento (decisión 039)
        elif m.get("max_amount") is not None:
            facts.append(f"cap {money(m['max_amount'])}")
        action = f"/panel/configuracion/herramientas/{tool.id}"
        checked = " checked" if m.get("requires_verified_contact") else ""
        always = " checked" if high else ""
        rules_form = f"""<form method="post" action="{action}">{hidden}
<input type="hidden" name="reglas" value="si">
{
            field(
                "Ask for a verified customer",
                f'<label class="row small" style="gap:8px;font-weight:400;margin:0"><input '
                f'type="checkbox" name="identidad" value="si"{checked}> Exige identidad '
                "verificada</label>",
                "The customer confirms who they are (a code sent to their email) before your "
                "agent can do this.",
                "On for anything that shows or changes an order",
            )
        }{
            field(
                "Always ask a person to approve",
                f'<label class="row small" style="gap:8px;font-weight:400;margin:0"><input '
                f'type="checkbox" name="siempre" value="si"{always}> Siempre pide aprobación'
                "</label>",
                "Every time, a person on your team approves it in Inbox → Approvals first.",
                "On for refunds",
            )
            if writes
            else ""
        }<div class="formbar"><button type="submit">Save rules</button></div></form>"""
        limits = ""
        if writes:
            over = (
                '<input value="Always (high risk)" disabled aria-label="Always (high risk)">'
                if high
                else f'<input id="sobre-{tool.id}" name="aprobacion_sobre" inputmode="decimal"'
                f' value="{esc(_plain(m.get("approval_over")))}" placeholder="Never">'
            )
            limits = f"""<form method="post" action="{action}">{hidden}
<input type="hidden" name="limites" value="si">
{
                field(
                    "Ask for approval above ($)",
                    over,
                    "Amounts over this wait for a person. Empty: never.",
                    "100",
                    f"sobre-{tool.id}",
                )
            }{
                field(
                    "Never allow above ($)",
                    f'<input id="tope-{tool.id}" name="monto_maximo" inputmode="decimal"'
                    f' value="{esc(_plain(m.get("max_amount")))}" placeholder="No limit">',
                    "Amounts over this are always rejected, even if a person approves.",
                    "500",
                    f"tope-{tool.id}",
                )
            }<div class="formbar"><button type="submit">Save limits</button></div></form>"""
        cards.append(
            f"""<div class="card"><div class="row"><div class="grow">
<b>{esc(tool_label(tool.name))}</b>
<span class="badge {"ok" if tool.approved else ""}">{"On" if tool.approved else "Off"}</span>
<div class="muted small data">{esc(m.get("description"))}</div>
<div class="muted small">{esc(" · ".join(facts))}</div></div>
<form method="post" action="{action}">{hidden}
<button class="{"" if tool.approved else "primary"}" name="aprobada"
 value="{"no" if tool.approved else "si"}">{"Turn off" if tool.approved else "Turn on"}
</button></form></div>
<details style="margin-top:10px"><summary>Rules and limits</summary>{rules_form}{limits}
</details></div>"""
        )
    return "".join(cards)


def topics_html(csrf: str, types: list[Any], approved: set[str]) -> str:
    """Los temas que atiende el agente (los tipos de caso del análisis, decisión 026)."""
    from app.panel.labels import tool_label

    if not types:
        return (
            '<div class="card empty"><b>No topics yet</b>Topics come from your support history. '
            'Upload it in Knowledge and run the analysis.<br><a class="btn primary" '
            'href="/panel/knowledge">Go to Knowledge</a></div>'
        )
    hidden = f'<input type="hidden" name="csrf" value="{csrf}">'
    cards = []
    for kind in types:
        missing = [t for t in kind.tools if t not in approved]
        warning = (
            '<div class="missing"><span>Uses actions that are off:</span> '
            f"{esc(', '.join(tool_label(t) for t in missing))}</div>"
            if missing
            else ""
        )
        used = ", ".join(tool_label(t) for t in kind.tools)
        uses = (
            f'<span>Actions:</span> <span class="data">{esc(used)}</span>'
            if used
            else "<span>Answers with information only</span>"
        )
        action = f"/panel/configuracion/tipos/{kind.id}"
        cards.append(
            f"""<div class="card"><div class="row"><div class="grow">
<b class="data">{esc(type_label(kind.name))}</b>
<span class="badge {"ok" if kind.active else ""}">{"On" if kind.active else "Off"}</span>
<div class="muted small data">{esc(kind.goal)}</div>
<div class="muted small"><span>{kind.volume}</span> <span>conversations in your history</span> ·
{uses}</div>{warning}</div>
<form method="post" action="{action}">{hidden}
<button class="{"" if kind.active else "primary"}" name="activo"
 value="{"no" if kind.active else "si"}">{"Turn off" if kind.active else "Turn on"}</button>
</form></div>
<details style="margin-top:10px"><summary>Instructions</summary>
<form method="post" action="{action}">{hidden}
{
                field(
                    "How your agent handles this topic",
                    f'<textarea id="ins-{kind.id}" name="instrucciones" class="mono"'
                    f' style="min-height:190px">{esc(kind.instructions)}</textarea>',
                    "Step-by-step instructions. Changes apply to the next conversation.",
                    "If the order is late, apologize and give the new date.",
                    f"ins-{kind.id}",
                )
            }<div class="formbar"><button type="submit">Save instructions</button></div>
</form></details></div>"""
        )
    return "".join(cards)


def language_html(csrf: str, language: str, names: dict[str, str]) -> str:
    options = "".join(
        f'<option value="{code}"{" selected" if code == language else ""}>{esc(name)}</option>'
        for code, name in names.items()
    )
    return f"""<div class="card"><form method="post" action="/panel/configuracion/idioma">
<input type="hidden" name="csrf" value="{csrf}">
{
        field(
            "Language your agent speaks",
            f'<select id="idioma" name="idioma" style="max-width:260px">{options}</select>',
            "Your agent answers, the chat and the verification email use this language. In "
            "Spanish, your agent writes in Rioplatense Spanish (vos).",
            "English, for a store in the United States",
            "idioma",
        )
    }
<div class="formbar"><button class="primary" type="submit">Save</button></div></form></div>"""
