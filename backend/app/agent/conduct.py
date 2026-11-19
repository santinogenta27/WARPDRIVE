"""Controles de conducta del agente en código (Hito 8, tarea 5; decisión 040).

Lo que el prompt pide y un modelo chico no siempre cumple se controla sobre la respuesta antes
de mandarla, como el control de fundamento:

* **Acciones afirmadas sin herramienta.** "I've added free express shipping" sin que una
  herramienta lo haya hecho en el turno es una respuesta inventada: se rechaza (lo hizo
  gpt-oss-20b en la prueba de la demo, desde una consulta general sin herramientas).
* **Empatía.** Si el cliente está molesto, la respuesta reconoce cómo se siente antes de
  resolver. Por ahora en inglés: el agente en español no cambia (su línea base es de antes).
"""

import re
from typing import Any

# "I've added", "I just started", "has been activated", "is now cancelled".
_CLAIM_EN = re.compile(
    r"(?i)\b(?:i(?:'ve|\s+have|\s+just)?|we(?:'ve|\s+have)?)\s+(?:just\s+|already\s+)?"
    r"(?:added|applied|activated|issued|created|started|processed|refunded|cancel+ed|changed"
    r"|updated|upgraded|set\s+up|sent|credited)\b"
    r"|\b(?:has|have)\s+been\s+(?:added|applied|activated|issued|created|started|processed"
    r"|refunded|cancel+ed|changed|updated|upgraded|set\s+up|credited)\b"
    r"|\bis\s+now\s+(?:active|cancel+ed|refunded|applied)\b"
)
# Primera persona ("te envié", "ya te reembolsamos") y pasivas ("está aplicado", "quedó
# acreditado"). "Fue enviado" queda afuera: es el estado de un pedido que trae la consulta. El
# plural sin "ya" también ("reembolsamos el total en 10 días" es una política).
_CLAIM_ES = re.compile(
    r"(?i)\b(?:ya\s+)?(?:te\s+|le\s+)?(?:agregu[eé]|apliqu[eé]|activ[eé]|inici[eé]|cargu[eé]"
    r"|proces[eé]|reembols[eé]|cancel[eé]|cambi[eé]|actualic[eé]|gener[eé]|envi[eé]"
    r"|acredit[eé])\b"
    r"|\bya\s+(?:te\s+|le\s+)?(?:lo\s+|la\s+)?(?:agreg|aplic|activ|inici|carg|proces|reembols"
    r"|cancel|cambi|actualiz|gener|envi|acredit)amos\b"
    r"|\b(?:qued[oó]|fue)\s+(?:ya\s+)?(?:agregad|aplicad|activad|iniciad|procesad"
    r"|reembolsad|cancelad|cambiad|actualizad|acreditad)[oa]s?\b"
    # "Está cancelado" puede ser el estado del pedido: con "está", solo lo que hizo la tienda.
    r"|\best[aá]\s+(?:ya\s+)?(?:aplicad|acreditad)[oa]s?\b"
    r"|\bya\s+est[aá]\s+reembolsad[oa]s?\b"
)
# Lo que dice que algo ya está terminado. Una aprobación pendiente no lo respalda: hace falta
# que la herramienta se haya ejecutado (decisión 048).
_DONE_EN = re.compile(
    r"(?i)\b(?:refunded|credited|cancel+ed|applied)\b"
    r"|\b(?:has|have)\s+been\s+(?:processed|issued)\b"
)
_DONE_ES = re.compile(
    r"(?i)\b(?:reembols[eé]|acredit[eé]|cancel[eé]|apliqu[eé])\b"
    r"|\b(?:reembols|acredit|cancel|aplic)amos\b"
    r"|\b(?:reembolsad|acreditad|cancelad|aplicad|procesad)[oa]s?\b"
)
DONE = {"es": _DONE_ES, "en": _DONE_EN}
# Lo que ya estaba hecho de antes no es una afirmación nueva ("the express shipping you chose
# earlier is already on your account").
_RECAP = re.compile(r"(?i)\b(?:already|earlier|previously|before|ya\s+estaba|antes)\b")

_UPSET_EN = re.compile(
    r"(?i)\b(?:ridiculous|unacceptable|frustrat\w*|angry|upset|annoyed|terrible|awful|worst"
    r"|furious|disappointed|fed\s+up|seriously|still\s+(?:hasn'?t|has\s+not|not|haven'?t)"
    r"|where\s+the\s+hell|what\s+the\s+hell|joke)\b|!!|\?!|!\?"
)
_EMPATHY_EN = re.compile(
    r"(?i)\b(?:sorry|apologi[sz]e|apologies|understand|frustrat\w*|hear\s+you|that'?s\s+not"
    r"\s+the\s+experience|i\s+know\s+how|must\s+be|really\s+appreciate\s+your\s+patience)\b"
)
# En español, solo para no ofrecer ventas a un cliente enojado (decisión 061): el control de
# empatía sigue solo en inglés.
_UPSET_ES = re.compile(
    r"(?i)\b(?:indignad[oa]|enojad[oa]|furios[oa]|inaceptable|vergüenza|vergonzoso|hart[oa]"
    r"|p[eé]sim[oa]|un desastre|estafa|una burla|nunca\s+(?:me\s+)?responden|todav[ií]a\s+no"
    r"\s+(?:lleg[oó]|me))\b|!!|\?!|!\?"
)
CLAIMS = {"es": _CLAIM_ES, "en": _CLAIM_EN}
UPSET = {"en": _UPSET_EN}
UPSET_FOR_OFFERS = (_UPSET_EN, _UPSET_ES)


# Los modelos reales escriben "I’ve" (apóstrofo tipográfico), "NW‑4528" (guion que no corta) o
# "10 %" (espacio angosto): sin normalizar, "I’ve started the refund" no se reconocía como una
# acción afirmada (Hito QA, decisión 066).
_TYPOGRAPHIC = str.maketrans({"’": "'", "‘": "'", "‑": "-", " ": " ", " ": " ", " ": " "})


def _plain(text: str) -> str:
    return text.translate(_TYPOGRAPHIC)


# Pedir el número de pedido que el cliente ya dio (gpt-oss-20b en los evals reales, nw11:
# "Where is my order NW-4550?" -> "I need the order number"). Decisión 066.
# Un pedido escrito como tal ("NW-4550", "#1005", "pedido 1005", "order 4550"), no cualquier
# número (un código postal o un teléfono no lo son).
_ORDER_IN_TEXT = re.compile(
    r"(?i)\b(?:order|pedido)\s+(?:n[uú]mero\s+)?#?\s*([a-z]{0,3}-?\d{3,6})(?!\d)"
    r"|(?<![\w-])([a-z]{1,3}-\d{3,6}|#\d{3,6})(?!\d)"
)
_ASKS_ORDER = re.compile(
    r"(?i)\b(?:order\s+(?:number|id|#)|n[uú]mero\s+(?:de(?:l)?\s+)?pedido)\b[^?]*\?"
    r"|\?[^?]*\b(?:order\s+(?:number|id)|n[uú]mero\s+de\s+pedido)\b"
)
GIVEN_ORDER_CORRECTIONS = {
    "es": (
        "Respuesta rechazada: el cliente ya dio el número de pedido ({order}). Usalo con la "
        "herramienta en lugar de volver a pedirlo."
    ),
    "en": (
        "Answer rejected: the customer already gave the order number ({order}). Use it with the "
        "tool instead of asking for it again."
    ),
}


def asks_given_order(reply: str, message: str) -> str | None:
    """El número de pedido que el cliente ya dio en su mensaje y la respuesta vuelve a pedir, o
    None."""
    found = _ORDER_IN_TEXT.search(_plain(message))
    if found is None or _ASKS_ORDER.search(_plain(reply)) is None:
        return None
    return (found.group(1) or found.group(2)).strip()


def upset_any_language(message: str) -> bool:
    """El cliente está molesto, escriba en inglés o en español."""
    return any(pattern.search(_plain(message)) for pattern in UPSET_FOR_OFFERS)


EMPATHY = {"en": _EMPATHY_EN}
ACTION_CLAIM_REASON = "afirma una acción que ninguna herramienta hizo en este turno"
TONE_CORRECTIONS = {
    "en": (
        "Answer rejected: the customer is upset. Start with one short sentence that acknowledges "
        "how they feel (for example, that you're sorry about the trouble), then give the "
        "solution. Keep everything else."
    ),
}
ACTION_CORRECTIONS = {
    "es": (
        "Respuesta rechazada: dice que hiciste algo que ninguna herramienta hizo en este turno. "
        "Si el cliente lo quiere, usá la herramienta que lo hace; si no tenés esa herramienta, "
        "decí qué se puede hacer, sin afirmar que ya está hecho."
    ),
    "en": (
        "Answer rejected: it says you did something no tool did in this turn. If the customer "
        "wants it, use the tool that does it; if you don't have that tool, say what can be done "
        "without saying it's already done."
    ),
}


# El agente nunca cobra (decisión 060): ninguna herramienta cobra ni compra por el cliente, así
# que decir que se cobró o que la compra ya está hecha es siempre falso.
_PAYMENT = re.compile(
    r"(?i)\b(?:i(?:'ve|\s+have)?|we(?:'ve|\s+have)?)\s+(?:just\s+|already\s+)?(?:charged|billed"
    r"|placed\s+(?:your|the)\s+order|purchased|bought|completed\s+(?:your|the)\s+purchase)\b"
    r"|\b(?:your\s+)?(?:card|payment)\s+(?:has\s+been|was)\s+(?:charged|processed|taken)\b"
    r"|\byour\s+order\s+(?:has\s+been|is)\s+placed\b"
    r"|\b(?:ya\s+)?te\s+cobr(?:[eé]|amos)\b|\bya\s+(?:est[aá]\s+)?(?:hecha|realizada)\s+la\s+compra\b"
    r"|\b(?:compr[eé]|compramos)\s+por\s+vos\b|\b(?:el\s+)?pago\s+(?:qued[oó]|fue)\s+(?:hecho"
    r"|realizado|acreditado|procesado)\b"
)
PAYMENT_REASON = "dice que cobró o que hizo la compra: el agente nunca cobra"
PAYMENT_CORRECTIONS = {
    "es": (
        "Respuesta rechazada: vos nunca cobrás ni comprás por el cliente. Mandale el link de "
        "compra y explicale que paga en el checkout de la tienda."
    ),
    "en": (
        "Answer rejected: you never charge or buy for the customer. Send the checkout link and "
        "explain that they pay on the store's checkout."
    ),
}


# -- ventas: lo que el modelo real dejaba a medias (evals reales del 19/11, decisión 071) -------
_MONEY = re.compile(
    r"(?i)(?:US)?\$\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:USD|ARS|dollars?|d[oó]lares|pesos)\b"
)
# "Let me add it to your cart and send you the checkout link", "te lo agrego al carrito".
_PROMISES_CART = re.compile(
    r"(?i)\b(?:let\s+me|i'?ll|i\s+will|i\s+can)\s+(?:go\s+ahead\s+and\s+|now\s+)?(?:add|put|send"
    r"|create|generate|get)\b[^.!?]{0,50}\b(?:cart|checkout|link)\b"
    r"|\b(?:te\s+lo\s+agrego|lo\s+agrego\s+al\s+carrito|te\s+(?:paso|mando|env[ií]o)\s+el\s+link"
    r"|te\s+armo\s+el\s+carrito)\b"
)
SALES_CORRECTIONS = {
    "price": {
        "es": "Decí el precio de cada producto que recomendás, tal como lo devolvió la búsqueda.",
        "en": "Say the price of each product you recommend, exactly as the search returned it.",
    },
    "promise": {
        "es": (
            "No prometas armar el carrito ni mandar el link: si el cliente ya dijo que lo "
            "quiere, llamá a armar_carrito ahora; si no, preguntale si lo quiere."
        ),
        "en": (
            "Don't promise to build the cart or send the link: if the customer already said they "
            "want it, call armar_carrito now; if not, ask whether they want it."
        ),
    },
    "offer": {
        "es": (
            "El carrito está listo y hay una venta cruzada para este momento: ofrecé {products} "
            "ahora con ofrecer_producto, una sola vez, y mandá el link del carrito."
        ),
        "en": (
            "The cart is ready and there's a cross-sell for this moment: offer {products} now "
            "with ofrecer_producto, only once, and send the cart link."
        ),
    },
}


def products_in(results: list[Any]) -> list[str]:
    """Los nombres de los productos que devolvió la búsqueda (vistas con `name` y `variants`)."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("name") and "variants" in value:
                found.append(str(value["name"]))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(results)
    return found


def sales_correction(
    reply: str,
    results: list[Any],
    *,
    cart_built: bool,
    offers: list[str],
    offered: bool,
    language: str,
) -> str | None:
    """Lo que falta en una respuesta de ventas, como corrección para el modelo, o None:
    - recomienda un producto de la búsqueda sin su precio;
    - promete armar el carrito o mandar el link sin haberlo hecho;
    - armó el carrito y no ofreció la venta cruzada disponible (el guardián controla el tope)."""
    text = _plain(reply)
    lang = "en" if language == "en" else "es"
    wanted: list[str] = []
    names = products_in(results)
    if names and any(n.lower() in text.lower() for n in names) and not _MONEY.search(text):
        wanted.append(SALES_CORRECTIONS["price"][lang])
    if not cart_built and _PROMISES_CART.search(text):
        wanted.append(SALES_CORRECTIONS["promise"][lang])
    if cart_built and offers and not offered:
        wanted.append(SALES_CORRECTIONS["offer"][lang].format(products=", ".join(offers)))
    if not wanted:
        return None
    lead = "Respuesta rechazada." if lang == "es" else "Answer rejected."
    return " ".join([lead, *wanted])


# Retención (decisión 071): el agente ya ofreció una alternativa y el cliente insiste.
_ALTERNATIVE = re.compile(
    r"(?i)\b(?:coupon|cup[oó]n|discount|descuento|store\s+credit|cr[eé]dito|exchange|cambio)\b"
)
_INSISTS = re.compile(
    r"(?i)\b(?:anyway|still\s+want|no,?\s+thanks?|no\s+thank\s+you|just\s+cancel|cancel\s+it"
    r"|go\s+ahead|igual(?:mente)?|de\s+todas\s+(?:formas|maneras)|no,?\s+gracias"
    r"|cancel[aá]lo|quiero\s+cancelar|reembols[aá]lo)\b"
)


def offers_alternative(text: str) -> bool:
    return _ALTERNATIVE.search(_plain(text)) is not None


def insists(text: str) -> bool:
    return _INSISTS.search(_plain(text)) is not None


def claims_payment(text: str) -> bool:
    """La respuesta dice que se cobró o que la compra ya está hecha."""
    return _PAYMENT.search(_plain(text)) is not None


def claims_action(text: str, language: str) -> bool:
    """La respuesta dice que ya se hizo una acción (agregar, aplicar, iniciar, cancelar…)."""
    text = _plain(text)
    found = CLAIMS.get(language, _CLAIM_EN).search(text)
    return found is not None and _RECAP.search(text) is None


def claims_completion(text: str, language: str) -> bool:
    """Además, dice que la acción ya terminó ("reembolsado", "applied"): una aprobación
    pendiente no alcanza, hace falta una herramienta ejecutada."""
    done = DONE.get(language, _DONE_EN).search(_plain(text))
    return claims_action(text, language) and done is not None


def is_upset(message: str, language: str) -> bool:
    pattern = UPSET.get(language)
    return pattern is not None and pattern.search(_plain(message)) is not None


def shows_empathy(text: str, language: str) -> bool:
    pattern = EMPATHY.get(language)
    return pattern is None or pattern.search(_plain(text)) is not None
