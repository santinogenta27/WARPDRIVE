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
    # login
    "Sign in": "Ingresar",
    "Password": "Clave",
    "Manage your AI agent, conversations and results.": (
        "Administrá tu agente de IA, sus conversaciones y sus resultados."
    ),
    "New here?": "¿Primera vez?",
    "Create your account": "Creá tu cuenta",
    # home
    "What your agent is doing and what needs you.": (
        "Qué está haciendo tu agente y qué te necesita."
    ),
    # inbox
    "No drafts waiting": "No hay borradores esperando",
    "With review before sending on, every answer from your agent waits here until someone on "
    "your team sends, edits or rejects it.": (
        "Con la revisión antes de enviar activada, cada respuesta de tu agente espera acá hasta "
        "que alguien de tu equipo la envía, la edita o la rechaza."
    ),
    "Review settings": "Ver la configuración",
    "Answers your agent wrote that wait for a person before they reach the customer.": (
        "Respuestas de tu agente que esperan a una persona antes de llegar al cliente."
    ),
    # test & improve
    "Chat with your agent as a customer would. Next to the chat you see each step it takes: what "
    "it understood, what it looked up and why it answered that way.": (
        "Chateá con tu agente como lo haría un cliente. Al lado del chat ves cada paso: qué "
        "entendió, qué consultó y por qué respondió así."
    ),
    "Open in a new tab": "Abrir en otra pestaña",
    "Simulated customers talk to your agent before real ones do, and a reviewer checks every "
    "conversation.": (
        "Clientes simulados conversan con tu agente antes que los reales, y un revisor controla "
        "cada conversación."
    ),
    # agent
    "When your agent answers, when it hands off to your team, and how much it can do on its own.": (
        "Cuándo atiende tu agente, cuándo deriva a tu equipo y cuánto puede hacer por su cuenta."
    ),
    "Business hours and hand-offs": "Horario y derivaciones",
    "Review before sending": "Revisar antes de enviar",
    "Answer check": "Control de respuestas",
    "What your agent can do in your store, like looking up an order or starting a refund. "
    "Nothing runs until you approve it.": (
        "Lo que tu agente puede hacer en tu tienda, como consultar un pedido o iniciar un "
        "reembolso. Nada se ejecuta hasta que lo aprobás."
    ),
    "The kinds of questions your customers ask, learned from your history. Turn on the ones "
    "your agent should handle.": (
        "Los tipos de consulta de tus clientes, aprendidos de tu historial. Activá los que tiene "
        "que atender tu agente."
    ),
    "How your agent sounds: its tone, the words it uses and how it signs off.": (
        "Cómo suena tu agente: su tono, las palabras que usa y cómo firma."
    ),
    "Products your agent can suggest after it solves the customer's question.": (
        "Productos que tu agente puede sugerir después de resolver la consulta del cliente."
    ),
    "The language your agent uses with your customers. Your own dashboard language is in the "
    "menu.": (
        "El idioma que usa tu agente con tus clientes. El idioma de tu panel está en el menú."
    ),
    # deploy
    "Connect your store, put the agent on your website and WhatsApp, and go live.": (
        "Conectá tu tienda, poné el agente en tu sitio y en WhatsApp, y salí en vivo."
    ),
    "Connect your store": "Conectá tu tienda",
    "Add the chat to your website": "Agregá el chat a tu sitio",
    "This company has no web chat.": "Esta empresa no tiene un chat web.",
    # settings
    "What your agent costs this month, your spending cap and the AI model it uses.": (
        "Cuánto cuesta tu agente este mes, tu tope de gasto y el modelo de IA que usa."
    ),
    "Your data is yours: take it with you or delete it.": (
        "Tus datos son tuyos: llevátelos o borralos."
    ),
    "The demo company is managed from": "La empresa de demo se administra desde",
    "Export your data": "Exportá tus datos",
    "Download .zip": "Descargar .zip",
    "Delete your company": "Borrar tu empresa",
    "It can't be undone.": "No se puede deshacer.",
    "Type": "Escribí",
    "to confirm": "para confirmar",
    "Delete company and all data": "Borrar la empresa y todos sus datos",
    # operador
    "The public demo page that shows each step of a case next to the chat.": (
        "La página pública de la demo que muestra cada paso de un caso al lado del chat."
    ),
}


def _normal(text: str) -> str:
    return " ".join(text.split())


_PHRASES = {_normal(k): v for k, v in PHRASES.items()}

# Textos con partes variables: (patrón en inglés, armado en español).
PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (re.compile(r"(\d+) waiting for approval"), lambda m: f"{m[1]} esperando aprobación"),
    (
        re.compile(
            r"Download everything (.+) has here \(conversations, customers, knowledge, rules and "
            r"settings\) as a \.zip file\. Passwords and keys are never included\."
        ),
        lambda m: (
            f"Descargá todo lo que {m[1]} tiene acá (conversaciones, clientes, conocimiento, "
            "reglas y configuración) en un .zip. Nunca incluye claves."
        ),
    ),
    (
        re.compile(
            r"This permanently deletes (.+) and all its data: conversations, customers, "
            r"knowledge, users and connections\."
        ),
        lambda m: (
            f"Esto borra para siempre {m[1]} y todos sus datos: conversaciones, clientes, "
            "conocimiento, usuarios y conexiones."
        ),
    ),
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
