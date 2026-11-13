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
_CLAIM_ES = re.compile(
    r"(?i)\b(?:ya\s+)?(?:te\s+|le\s+)?(?:agregu[eé]|apliqu[eé]|activ[eé]|inici[eé]|cargu[eé]"
    r"|proces[eé]|reembols[eé]|cancel[eé]|cambi[eé]|actualic[eé]|gener[eé])\b"
    r"|\b(?:qued[oó]|fue)\s+(?:agregad|aplicad|activad|iniciad|procesad|reembolsad|cancelad"
    r"|cambiad|actualizad)[oa]s?\b"
)
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


def claims_action(text: str, language: str) -> bool:
    """La respuesta dice que ya se hizo una acción (agregar, aplicar, iniciar, cancelar…)."""
    found = CLAIMS.get(language, _CLAIM_EN).search(text)
    return found is not None and _RECAP.search(text) is None


def is_upset(message: str, language: str) -> bool:
    pattern = UPSET.get(language)
    return pattern is not None and pattern.search(message) is not None


def shows_empathy(text: str, language: str) -> bool:
    pattern = EMPATHY.get(language)
    return pattern is None or pattern.search(text) is not None
