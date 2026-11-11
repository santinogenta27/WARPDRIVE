"""Voseo rioplatense en las respuestas del agente.

El prompt pide voseo, pero el modelo a veces se escapa al tuteo ("indícanos", "dime"). Antes de
enviar, las formas de "tú" más comunes y sin ambigüedad se pasan a "vos". Solo formas que no
coinciden con otra persona del verbo: "indica" o "revisa" pueden ser "él indica", así que no se
tocan.
"""

import re

_FORMS = {
    # Pronombres
    "tú": "vos",
    "ti": "vos",
    "contigo": "con vos",
    "eres": "sos",
    # Presente
    "puedes": "podés",
    "quieres": "querés",
    "tienes": "tenés",
    "necesitas": "necesitás",
    "debes": "debés",
    "sabes": "sabés",
    "deseas": "deseás",
    "prefieres": "preferís",
    "recibes": "recibís",
    "decides": "decidís",
    "buscas": "buscás",
    "esperas": "esperás",
    # Imperativos con pronombre
    "dime": "decime",
    "dinos": "decinos",
    "indícanos": "indicanos",
    "indícame": "indicame",
    "envíanos": "envianos",
    "envíame": "enviame",
    "escríbenos": "escribinos",
    "escríbeme": "escribime",
    "cuéntame": "contame",
    "cuéntanos": "contanos",
    "confírmame": "confirmame",
    "confírmanos": "confirmanos",
    "pásame": "pasame",
    "pásanos": "pasanos",
    "avísame": "avisame",
    "avísanos": "avisanos",
    "mándame": "mandame",
    "mándanos": "mandanos",
    "déjame": "dejame",
    "ayúdame": "ayudame",
    "compárteme": "compartime",
    "compártenos": "compartinos",
    "proporciónanos": "pasanos",
    "proporcióname": "pasame",
    "facilítanos": "pasanos",
    "verifícalo": "verificalo",
    "revísalo": "revisalo",
    "ten en cuenta": "tené en cuenta",
    "haz": "hacé",
}
_PATTERN = re.compile(
    r"(?<![\w*])(" + "|".join(sorted(map(re.escape, _FORMS), key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)


def _replace(match: re.Match[str]) -> str:
    word = match.group(0)
    replacement = _FORMS[word.lower()]
    return replacement[0].upper() + replacement[1:] if word[0].isupper() else replacement


def to_voseo(text: str) -> str:
    return _PATTERN.sub(_replace, text)
