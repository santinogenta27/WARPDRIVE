"""El panel en español (decisión 085).

Desde el Hito 13 el panel sale en inglés por defecto y cada usuario elige su idioma. Las
pantallas nuevas se escriben en inglés y este catálogo las pasa al español; las de antes siguen
escritas en español y `english.py` las pasa al inglés. Funciona igual que `english.py`: cada nodo
de texto (y `placeholder`, `aria-label`, `title`) se busca entero, con los espacios normalizados,
y lo que no está queda igual.
"""

import re
from collections.abc import Callable

PHRASES: dict[str, str] = {
    # layout
    "Skip to content": "Ir al contenido",
    "Open menu": "Abrir el menú",
    "Main": "Principal",
    "Sections": "Secciones",
    "Section": "Sección",
    "Company": "Empresa",
    "Operator": "Operador",
    "Sign out": "Salir",
    "Dark theme": "Tema oscuro",
    "Light theme": "Tema claro",
    # secciones
    "Home": "Inicio",
    "Inbox": "Bandeja",
    "Customers": "Clientes",
    "Agent": "Agente",
    "Knowledge": "Conocimiento",
    "Deploy": "Publicar",
    "Test & Improve": "Probar y mejorar",
    "Insights": "Métricas",
    "Settings": "Ajustes",
    # pestañas
    "Conversations": "Conversaciones",
    "Approvals": "Aprobaciones",
    "Drafts": "Borradores",
    "Rules": "Reglas",
    "Actions": "Acciones",
    "Topics": "Temas",
    "Brand & voice": "Marca y voz",
    "Sales": "Ventas",
    "Language": "Idioma",
    "Readiness": "Preparación",
    "Playground": "Probar el chat",
    "Simulations": "Simulaciones",
    "To improve": "Para mejorar",
    "Overview": "Resumen",
    "Results": "Resultados",
    "Team": "Equipo",
    "Usage & model": "Uso y modelo",
    "Your data": "Tus datos",
    # operador
    "Companies": "Empresas",
    "Trials": "Pruebas",
    "Billing": "Facturación",
    "Behind the scenes": "Detrás de escena",
}


def _normal(text: str) -> str:
    return " ".join(text.split())


_PHRASES = {_normal(k): v for k, v in PHRASES.items()}

# Textos con partes variables: (patrón en inglés, armado en español).
PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (re.compile(r"(\d+) waiting for approval"), lambda m: f"{m[1]} esperando aprobación"),
]


def translate_text(text: str) -> str:
    """Un texto entero en español, si está en el catálogo; si no, igual."""
    key = _normal(text)
    if not key:
        return text
    if key in _PHRASES:
        return _PHRASES[key]
    for pattern, replace in PATTERNS:
        found = pattern.fullmatch(key)
        if found:
            return replace(found)
    return text
