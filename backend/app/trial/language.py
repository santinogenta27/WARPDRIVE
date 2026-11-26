"""En qué idioma escribe el visitante de la prueba gratis (decisión 101).

El agente contesta en el idioma del visitante, sin importar si entró por /try o por /probar. Sin
modelo: palabras frecuentes y marcas de cada idioma. Un mensaje que no lo deja claro (un número de
pedido, un código) devuelve None y manda el idioma de los mensajes anteriores.
"""

import re

_WORD = re.compile(r"[a-záéíóúñü']+")
_SPANISH = set(
    (
        "hola quiero quisiera dónde donde está esta mi pedido devolver tienen tenés tenes busco "
        "cuánto cuanto cuesta precio envío envio gracias por favor que qué como cómo el la los "
        "las un una de del para con sin es son hay me necesito puedo podés talle cambio "
        "reembolso descuento buenas buen día dia sí si no y o pero muy también cuándo cuando "
        "llega compra producto "
    ).split()
)
_ENGLISH = set(
    (
        "hi hello hey i i'm im want would like where is my order return do you have looking for "
        "how much does it cost price shipping thanks thank please what the a an of to with "
        "without are there me need can could size exchange refund discount good morning yes and "
        "or but very also when will arrive buy product get your any this that "
    ).split()
)
_SPANISH_MARKS = re.compile(r"[ñ¿¡]|ción\b|[áéíóú]")


def detect(text: str) -> str | None:
    """ "en", "es" o None si el mensaje no alcanza para saberlo."""
    lowered = text.lower()
    words = _WORD.findall(lowered)
    spanish = sum(w in _SPANISH for w in words) + 2 * len(_SPANISH_MARKS.findall(lowered))
    english = sum(w in _ENGLISH for w in words)
    if spanish == english:
        return None
    return "es" if spanish > english else "en"


def choose(text: str, previous: list[str], default: str) -> str:
    """El idioma del mensaje; si no es claro, el de los anteriores (del más nuevo al más viejo);
    si tampoco, el de la prueba."""
    for candidate in [text, *previous]:
        found = detect(candidate)
        if found is not None:
            return found
    return "en" if default == "en" else "es"
