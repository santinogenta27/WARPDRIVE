"""Formato de texto para WhatsApp."""

import re

# **texto** de markdown, sin espacios pegados a los asteriscos ni saltos de línea en el medio.
_MARKDOWN_BOLD = re.compile(r"\*\*(?=\S)([^\n]*?\S)\*\*")


def to_whatsapp(text: str) -> str:
    """Convierte la negrita de markdown (**texto**) a la de WhatsApp (*texto*)."""
    return _MARKDOWN_BOLD.sub(r"*\1*", text)
