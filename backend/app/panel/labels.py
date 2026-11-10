"""Nombres legibles de los tipos de caso para el panel ("Dónde está mi pedido")."""

# Etiquetas del clasificador que no son tipos de caso de la empresa.
SPECIAL = {
    "conversacion_general": "Conversación general",
    "consulta_general": "Consulta general",
    "derivar_humano": "Pidió hablar con una persona",
    "pedido_no_permitido": "Pedido no permitido",
    "otro": "Otro tema",
}
# Los nombres de los tipos van sin tildes (son identificadores): se restituyen para mostrarlos.
ACCENTS = {
    "donde": "dónde",
    "esta": "está",
    "como": "cómo",
    "cuando": "cuándo",
    "cuanto": "cuánto",
    "direccion": "dirección",
    "facturacion": "facturación",
    "devolucion": "devolución",
    "cancelacion": "cancelación",
    "informacion": "información",
    "envio": "envío",
    "envios": "envíos",
    "credito": "crédito",
    "debito": "débito",
    "garantia": "garantía",
    "politica": "política",
    "politicas": "políticas",
    "codigo": "código",
    "atencion": "atención",
    "promocion": "promoción",
    "suscripcion": "suscripción",
    "dias": "días",
}


def type_label(name: str | None) -> str:
    if not name:
        return "Sin clasificar"
    if name in SPECIAL:
        return SPECIAL[name]
    words = [ACCENTS.get(w, w) for w in name.replace("-", "_").split("_") if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:]
