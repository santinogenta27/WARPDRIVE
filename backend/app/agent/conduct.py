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
CLAIMS = {"es": _CLAIM_ES, "en": _CLAIM_EN}
UPSET = {"en": _UPSET_EN}
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


def claims_payment(text: str) -> bool:
    """La respuesta dice que se cobró o que la compra ya está hecha."""
    return _PAYMENT.search(text) is not None


def claims_action(text: str, language: str) -> bool:
    """La respuesta dice que ya se hizo una acción (agregar, aplicar, iniciar, cancelar…)."""
    found = CLAIMS.get(language, _CLAIM_EN).search(text)
    return found is not None and _RECAP.search(text) is None


def claims_completion(text: str, language: str) -> bool:
    """Además, dice que la acción ya terminó ("reembolsado", "applied"): una aprobación
    pendiente no alcanza, hace falta una herramienta ejecutada."""
    done = DONE.get(language, _DONE_EN).search(text)
    return claims_action(text, language) and done is not None


def is_upset(message: str, language: str) -> bool:
    pattern = UPSET.get(language)
    return pattern is not None and pattern.search(message) is not None


def shows_empathy(text: str, language: str) -> bool:
    pattern = EMPATHY.get(language)
    return pattern is None or pattern.search(text) is not None
