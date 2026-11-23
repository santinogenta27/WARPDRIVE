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
    "How your chat looks": "Cómo se ve tu chat",
    "How your agent writes": "Cómo escribe tu agente",
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
    "Get started": "Primeros pasos",
    "Your agent goes live when these steps are done.": (
        "Tu agente sale en vivo cuando completes estos pasos."
    ),
    "Setup progress": "Avance de la configuración",
    "Done": "Hecho",
    "Start": "Empezar",
    "Needs your attention": "Te necesita",
    "Open Inbox": "Abrir la bandeja",
    "Approval": "Aprobación",
    "View conversation": "Ver conversación",
    "Reject": "Rechazar",
    "Approve": "Aprobar",
    "Handed off": "Derivado",
    "A customer is waiting for your team": "Un cliente espera a tu equipo",
    "Reply": "Responder",
    "Draft": "Borrador",
    "Draft to review": "Borrador por revisar",
    "Review": "Revisar",
    "You're all caught up": "Estás al día",
    "Nothing needs you right now. Approvals, hand-offs and drafts show up here.": (
        "Nada te necesita ahora. Acá aparecen las aprobaciones, derivaciones y borradores."
    ),
    "Last 7 days": "Últimos 7 días",
    "See all insights": "Ver todas las métricas",
    "Resolved by your agent": "Resueltas por tu agente",
    "Attributed sales": "Ventas atribuidas",
    "AI cost": "Costo de IA",
    "Agent status": "Estado del agente",
    "Live": "En vivo",
    "Paused": "En pausa",
    "Setting up": "Configurándose",
    "Your agent is answering your customers.": "Tu agente está atendiendo a tus clientes.",
    "Your agent isn't answering. Customers are told a person will reply.": (
        "Tu agente no está atendiendo. A los clientes se les dice que responde una persona."
    ),
    "Finish the steps above to go live.": "Completá los pasos para salir en vivo.",
    "Pause agent": "Pausar el agente",
    "Resume": "Reanudar",
    "Website chat": "Chat del sitio",
    "Store": "Tienda",
    "Installed": "Instalado",
    "Not installed": "Sin instalar",
    "Connected": "Conectado",
    "Not active": "Inactivo",
    "Not connected": "Sin conectar",
    "Demo store": "Tienda de demo",
    "Test mode": "Modo de prueba",
    "Manage channels": "Administrar canales",
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
    # deploy (decisión 086)
    "So your agent can look up orders, products and customers.": (
        "Para que tu agente consulte pedidos, productos y clientes."
    ),
    "Choose what your agent can do": "Elegí qué puede hacer tu agente",
    "Turn on the actions it can take in your store.": (
        "Activá las acciones que puede hacer en tu tienda."
    ),
    "Paste one line of code on your site.": "Pegá una línea de código en tu sitio.",
    "Connect WhatsApp": "Conectá WhatsApp",
    "Optional: the same agent answers on your WhatsApp number.": (
        "Opcional: el mismo agente atiende en tu número de WhatsApp."
    ),
    "Go live": "Salí en vivo",
    "When everything is ready, your agent starts answering customers.": (
        "Cuando todo está listo, tu agente empieza a atender a tus clientes."
    ),
    "Optional": "Opcional",
    "Ready": "Listo",
    "Not ready": "Falta preparar",
    "Connect your store first": "Primero conectá tu tienda",
    "None on yet": "Ninguna activa todavía",
    "Orders, refunds, coupons, cancellations and products": (
        "Pedidos, reembolsos, cupones, cancelaciones y productos"
    ),
    "Orders, coupons and products": "Pedidos, cupones y productos",
    "Other platform (API)": "Otra plataforma (API)",
    "Any system with an OpenAPI description": "Cualquier sistema con una descripción OpenAPI",
    "Store address": "Dirección de la tienda",
    "Example: acme-store.myshopify.com": "Ejemplo: acme-store.myshopify.com",
    "How do you want to connect?": "¿Cómo querés conectarla?",
    "App from the Dev Dashboard: Client ID and Client secret (recommended)": (
        "App del Dev Dashboard: Client ID y Client secret (recomendado)"
    ),
    "Access token of a legacy app your store already has (shpat_…)": (
        "Token de acceso de una app legacy que tu tienda ya tiene (shpat_…)"
    ),
    "Access token (legacy app only)": "Token de acceso (solo app legacy)",
    "Connect": "Conectar",
    "How to get the Client ID and secret": "Cómo conseguir el Client ID y el secret",
    "Install with Shopify": "Instalar con Shopify",
    "The fastest way: Shopify asks you to approve our app and you come back here connected. Or "
    "connect your own app below.": (
        "La forma más rápida: Shopify te pide aprobar nuestra app y volvés acá con la tienda "
        "conectada. O conectá tu propia app más abajo."
    ),
    "Store number": "Número de la tienda",
    "Access token": "Token de acceso",
    "How to get them": "Cómo conseguirlos",
    "For developers: your agent's actions are created from your API description.": (
        "Para desarrolladores: las acciones de tu agente se crean a partir de la descripción de "
        "tu API."
    ),
    "Upload your API description (OpenAPI, .json or .yaml).": (
        "Subí la descripción de tu API (OpenAPI, .json o .yaml)."
    ),
    "Or paste it here": "O pegala acá",
    "…or paste it here": "…o pegala acá",
    "Save API description": "Guardar la descripción",
    "Base URL (HTTPS)": "URL base (HTTPS)",
    "Authentication": "Autenticación",
    "No authentication": "Sin autenticación",
    "API key in a header": "Clave de API en un encabezado",
    "User and password (Basic)": "Usuario y clave (Basic)",
    "Header name (API key only)": "Nombre del encabezado (solo clave de API)",
    "Credential (token, API key or user:password)": (
        "Credencial (token, clave de API o usuario:clave)"
    ),
    "Saved. Leave it empty to keep it.": "Guardada. Dejalo vacío para conservarla.",
    "Stored encrypted and never shown again.": "Se guarda cifrada y no se vuelve a mostrar.",
    "Save": "Guardar",
    "Your agent uses your real store.": "Tu agente usa tu tienda real.",
    "Test mode: until you turn it on, your agent uses made-up answers and never touches your "
    "store.": (
        "Modo de prueba: hasta que lo actives, tu agente usa respuestas inventadas y nunca toca "
        "tu tienda."
    ),
    "Use my real store": "Usar mi tienda real",
    "Back to test mode": "Volver al modo de prueba",
    "Test connection": "Probar la conexión",
    "Connect a different store": "Conectar otra tienda",
    "The connection test failed.": "La prueba de conexión falló.",
    "What's missing": "Qué falta",
    "What to do": "Qué hacer",
    "A permission": "Un permiso",
    "A credential": "Una credencial",
    "The store": "La tienda",
    "Your system": "Tu sistema",
    "Your system (API)": "Tu sistema (API)",
    "This demo company uses a simulated store with test orders.": (
        "Esta empresa de demo usa una tienda simulada con pedidos de prueba."
    ),
    "Your agent's actions come from your store. Connect it in step 1 and they show up here.": (
        "Las acciones de tu agente salen de tu tienda. Conectala en el paso 1 y aparecen acá."
    ),
    "Nothing runs until you turn it on. High-risk actions, like a refund, always wait for a "
    "person's approval.": (
        "Nada se ejecuta hasta que lo activás. Las acciones de riesgo alto, como un reembolso, "
        "siempre esperan la aprobación de una persona."
    ),
    "On": "Activa",
    "Off": "Inactiva",
    "Choose actions and limits": "Elegir acciones y límites",
    "Your code": "Tu código",
    "Copy code": "Copiar código",
    "Copied": "Copiado",
    "Where to paste it": "Dónde pegarlo",
    "Any website": "Cualquier sitio",
    "Allowed domains": "Dominios permitidos",
    "The chat only works on these sites, one per line. Your connected store's domains are added "
    "automatically. Use *.yourstore.com for every subdomain.": (
        "El chat solo funciona en estos sitios, uno por línea. Los dominios de tu tienda "
        "conectada se agregan solos. Usá *.tutienda.com para todos los subdominios."
    ),
    "Save domains": "Guardar dominios",
    "Check installation": "Revisar la instalación",
    "Open your chat page": "Abrir la página de tu chat",
    "Domains saved.": "Dominios guardados.",
    "Add your website's domain under Allowed domains first.": (
        "Primero agregá el dominio de tu sitio en Dominios permitidos."
    ),
    "Connection test passed. You can now use your real store.": (
        "La prueba de conexión pasó. Ya podés usar tu tienda real."
    ),
    "Store connected. You can now use your real store.": (
        "Tienda conectada. Ya podés usar tu tienda real."
    ),
    # diagnóstico de la conexión (app/companies/deploy.py)
    "Connected to your store.": "Conectado a tu tienda.",
    "In the Dev Dashboard, open your app → Versions, add them under Scopes and click Release. "
    "Then click Connect again here.": (
        "En el Dev Dashboard, abrí tu app → Versions, agregalos en Scopes y tocá Release. "
        "Después tocá Conectar de nuevo acá."
    ),
    "Copy the Client ID and Client secret again from your app's Settings in the Dev Dashboard, "
    "check the app is installed on this store, and connect again.": (
        "Copiá de nuevo el Client ID y el Client secret desde Settings de tu app en el Dev "
        "Dashboard, revisá que la app esté instalada en esta tienda y conectá de nuevo."
    ),
    "Create a new access token for your Tiendanube app and paste it again.": (
        "Creá un token de acceso nuevo para tu app de Tiendanube y pegalo de nuevo."
    ),
    "Check the credential you saved for your system and save it again.": (
        "Revisá la credencial que guardaste para tu sistema y guardala de nuevo."
    ),
    "This store isn't in the same Shopify organization as your app.": (
        "Esta tienda no es de la misma organización de Shopify que tu app."
    ),
    "Create the app from the Dev Dashboard of the organization that owns the store, or use "
    "Install with Shopify.": (
        "Creá la app desde el Dev Dashboard de la organización dueña de la tienda, o usá "
        "Instalar con Shopify."
    ),
    "The Shopify store is paused or unpaid.": "La tienda de Shopify está pausada o sin pago.",
    "Reactivate the store.": "Reactivá la tienda.",
    "Check the store address (yourstore.myshopify.com) or the store number, and connect again.": (
        "Revisá la dirección de la tienda (tutienda.myshopify.com) o el número de tienda, y "
        "conectá de nuevo."
    ),
    "Check the base URL of your system.": "Revisá la URL base de tu sistema.",
    "Try again in a few minutes. If it keeps failing, check the base URL.": (
        "Probá de nuevo en unos minutos. Si sigue fallando, revisá la URL base."
    ),
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
    (re.compile(r"(\d+) of (\d+) done"), lambda m: f"{m[1]} de {m[2]} hechos"),
    (
        re.compile(r"(\d+) of (\d+) required steps done"),
        lambda m: f"{m[1]} de {m[2]} pasos obligatorios hechos",
    ),
    (re.compile(r"(\d+) of (\d+) on"), lambda m: f"{m[1]} de {m[2]} activas"),
    (re.compile(r"Connected to (.+)\."), lambda m: f"Conectado a {m[1]}."),
    (
        re.compile(r"Your Shopify app is missing these permissions: (.+)\."),
        lambda m: f"A tu app de Shopify le faltan estos permisos: {m[1]}.",
    ),
    (
        re.compile(r"(Shopify|Tiendanube|Your system) didn't accept the credentials\."),
        lambda m: f"{'Tu sistema' if m[1] == 'Your system' else m[1]} no aceptó las credenciales.",
    ),
    (
        re.compile(r"We couldn't find the store on (.+)\."),
        lambda m: f"No encontramos la tienda en {m[1]}.",
    ),
    (
        re.compile(r"(Shopify|Tiendanube|Your system) answered with an error \((.+)\)\."),
        lambda m: (
            f"{'Tu sistema' if m[1] == 'Your system' else m[1]} respondió con un error ({m[2]})."
        ),
    ),
    (
        re.compile(
            r"(Store connected|Connection test passed)\. We added (.+) to your chat's "
            r"allowed domains\. You can now use your real store\."
        ),
        lambda m: (
            f"{'Tienda conectada' if m[1] == 'Store connected' else 'La prueba de conexión pasó'}"
            f". Se agregaron {m[2]} a los dominios permitidos del chat. Ya podés usar tu tienda "
            "real."
        ),
    ),
    (
        re.compile(r"Your chat is installed: it loaded from (.+)\."),
        lambda m: f"Tu chat está instalado: cargó desde {m[1]}.",
    ),
    (
        re.compile(
            r"We found the code on (.+), but the chat hasn't loaded there yet\. Open your site in "
            r"a browser and check again\."
        ),
        lambda m: (
            f"Encontramos el código en {m[1]}, pero el chat todavía no cargó ahí. Abrí tu sitio en "
            "un navegador y revisá de nuevo."
        ),
    ),
    (
        re.compile(
            r"We couldn't find the code on (.+)\. Paste it right before </body> and publish your "
            r"site\."
        ),
        lambda m: (
            f"No encontramos el código en {m[1]}. Pegalo justo antes de </body> y publicá tu sitio."
        ),
    ),
    (
        re.compile(
            r"We couldn't open (.+)\. Check that the domain is right and your site is online\."
        ),
        lambda m: (
            f"No pudimos abrir {m[1]}. Revisá que el dominio esté bien y que tu sitio esté "
            "en línea."
        ),
    ),
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
