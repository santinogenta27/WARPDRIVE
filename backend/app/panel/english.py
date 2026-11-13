"""Panel en inglés (Hito 8, tarea 2; decisión 037).

El idioma del panel es el de la empresa de la sesión (`tenants.settings.language`). `enter` lo
fija en `LANGUAGE` al principio de cada pedido. Con `en`:

- Las ayudas comunes (estados, montos, fechas, tipos de caso, acciones) ya salen en inglés: ver
  `html.py` y `labels.py`.
- El resto del texto de cada página se traduce al final, en `page()`, con este catálogo: cada
  nodo de texto del HTML (y los atributos `placeholder`, `aria-label`, `title` y `data-done`) se
  busca entero, con los espacios normalizados. Lo que no está en el catálogo queda igual: los
  datos de la empresa y de sus clientes (mensajes, nombres, instrucciones) no se tocan.
- No se traduce adentro de `<script>`, `<style>`, `<textarea>` ni `<pre>`.

El diseño no cambia: es el mismo HTML con otro texto. El test de aceptación recorre cada
pantalla de una empresa en inglés y falla si queda texto en español.
"""

import html
import re
from collections.abc import Callable
from contextvars import ContextVar

LANGUAGE: ContextVar[str] = ContextVar("panel_language", default="es")


def set_language(language: str) -> None:
    LANGUAGE.set("en" if language == "en" else "es")


def english() -> bool:
    return LANGUAGE.get() == "en"


# -- textos enteros ------------------------------------------------------------------------------
PHRASES: dict[str, str] = {
    # comunes
    "Panel del equipo": "Team dashboard",
    "Salir": "Sign out",
    "Sin permiso": "Not allowed",
    "No existe": "Not found",
    "Panel": "Dashboard",
    "El formulario venció. Volvé a cargar la página.": "This form expired. Please reload the page.",
    "La empresa de la demo todavía no está cargada.": "The demo company isn't loaded yet.",
    "Esa aprobación no existe.": "That approval doesn't exist.",
    "Ese caso no existe.": "That case doesn't exist.",
    "Ese tipo de caso no existe.": "That case type doesn't exist.",
    "Esa herramienta no existe.": "That tool doesn't exist.",
    "Esa acción no existe.": "That action doesn't exist.",
    "Ese documento no existe.": "That document doesn't exist.",
    "Esa empresa no existe.": "That company doesn't exist.",
    "ver caso": "view case",
    "Cancelar": "Cancel",
    "Enviar": "Send",
    # login
    "Ingresar": "Sign in",
    "Ingresar al panel": "Sign in to the dashboard",
    "Panel del equipo de atención.": "Dashboard for the support team.",
    "Clave": "Password",
    "Falta configurar PANEL_EMAIL y PANEL_PASSWORD.": "PANEL_EMAIL and PANEL_PASSWORD aren't set.",
    "Demasiados intentos. Probá de nuevo en unos minutos.": (
        "Too many attempts. Please try again in a few minutes."
    ),
    "Email o clave incorrectos.": "Wrong email or password.",
    # menú
    "Casos": "Cases",
    "Aprobaciones": "Approvals",
    "Onboarding": "Onboarding",
    "Reporte": "Report",
    "Configuración": "Settings",
    "Métricas": "Metrics",
    "Empresas": "Companies",
    "Facturación": "Billing",
    # aprobaciones
    "Pendiente": "Pending",
    "Rechazar": "Reject",
    "Aprobar": "Approve",
    "Aprobado": "Approved",
    "Rechazado": "Rejected",
    "Decididas": "Decided",
    "No hay pedidos esperando aprobación.": "Nothing is waiting for approval.",
    "Las acciones de riesgo alto (como un reembolso) no se ejecutan hasta que una persona las "
    "aprueba. Al aprobar, se ejecuta por el guardián y el cliente recibe el aviso en el chat.": (
        "High-risk actions (like a refund) don't run until a person approves them. When you "
        "approve, the policy guard runs the action and the customer is notified in the chat."
    ),
    # casos
    "Derivados a una persona": "Handed off to a person",
    "Esperando aprobación": "Waiting for approval",
    "Abiertos": "Open",
    "Resueltos": "Resolved",
    "(sin texto)": "(no text)",
    "Todavía no hay conversaciones.": "No conversations yet.",
    "Reiniciar demo": "Reset demo",
    "Conversaciones con su estado: primero las derivadas a una persona, después las que esperan "
    "una aprobación. Un caso se resuelve cuando el cliente confirma o pasan 24 h sin "
    "respuesta.": (
        "Conversations and their status: handoffs to a person first, then the ones waiting for "
        "an approval. A case is resolved when the customer confirms or after 24 hours without a "
        "reply."
    ),
    # detalle del caso
    "← Casos": "← Cases",
    "Sin mensajes.": "No messages.",
    "Derivado a una persona": "Handed off to a person",
    "Costo del modelo": "Model cost",
    "Costo del modelo (modelo gratuito)": "Model cost (free model)",
    "pasos auditados": "audited steps",
    "Conversación": "Conversation",
    "Responder como persona del equipo": "Reply as a member of the team",
    "En el chat web el cliente lo ve en unos segundos.": (
        "In the web chat the customer sees it within a few seconds."
    ),
    "Auditoría paso a paso": "Step-by-step audit trail",
    "Cadena de auditoría íntegra": "Audit chain intact",
    "Mensaje del cliente": "Customer message",
    "Consultó al modelo": "Called the model",
    "Clasificó el caso": "Classified the case",
    "Buscó en los documentos": "Searched the documents",
    "Fuentes:": "Sources:",
    "Fuentes: sin resultados": "Sources: no results",
    "Usó una herramienta": "Used a tool",
    "Descartó una respuesta sin respaldo": "Discarded an unsupported answer",
    "Descartó una respuesta que no se podía mostrar": "Discarded an answer it couldn't show",
    "Respondió": "Answered",
    "Actuó en el sistema y respondió": "Took action in the system and answered",
    "Pidió aprobación a una persona": "Asked a person for approval",
    "Le avisó al cliente que lo atiende una persona": "Told the customer a person will help them",
    "Derivó a una persona": "Handed off to a person",
    "Respondió una persona del equipo": "A team member replied",
    "Aprobó una persona del equipo": "A team member approved",
    "Rechazó una persona del equipo": "A team member rejected",
    "Caso resuelto": "Case resolved",
    "Caso resuelto por el asistente": "Case resolved by the assistant",
    "Caso resuelto con una persona": "Case resolved with a person",
    "Satisfacción": "Satisfaction",
    "Caso reabierto": "Case reopened",
    "Tiempo de la respuesta": "Response time",
    "Dejó el pedido en revisión con el mensaje fijo": (
        "Left the request under review (fixed message)"
    ),
    "Verificación de identidad": "Identity verification",
    "Pidió el email de la compra para enviar un código": (
        "Asked for the purchase email to send a code"
    ),
    "Pidió el email de la compra y envió un código": "Asked for the purchase email and sent a code",
    "No se pudo enviar el código por email": "The code couldn't be sent by email",
    "Código correcto: identidad verificada": "Correct code: identity verified",
    "Código incorrecto": "Wrong code",
    "Demasiados intentos: verificación bloqueada": "Too many attempts: verification locked",
    "Código vencido": "Expired code",
    "Envió la respuesta": "Sent the answer",
    "Chat web": "Web chat",
    "El cliente confirmó.": "The customer confirmed.",
    "24 h sin respuesta del cliente": "24 hours without a reply from the customer",
    "El cliente dijo que su consulta se resolvió": "The customer said their question was resolved",
    "El cliente dijo que su consulta no se resolvió": (
        "The customer said their question wasn't resolved"
    ),
    "El cliente dijo que su consulta se resolvió (dejó un comentario)": (
        "The customer said their question was resolved (left a comment)"
    ),
    "El cliente dijo que su consulta no se resolvió (dejó un comentario)": (
        "The customer said their question wasn't resolved (left a comment)"
    ),
    "agente ·": "agent ·",
    "clasificador ·": "classifier ·",
    "· verificado": "· verified",
    "· sin verificar": "· not verified",
    # motivos internos (derivación, guardián, control de fundamento)
    "El cliente pidió hablar con una persona.": "The customer asked to talk to a person.",
    "El tipo de caso no está activo para la empresa.": (
        "The case type isn't active for the company."
    ),
    "El cliente amenaza con una denuncia o acción legal.": (
        "The customer is threatening a complaint or legal action."
    ),
    "El cliente respondió que su consulta no se resolvió.": (
        "The customer replied that their question wasn't resolved."
    ),
    "El modelo no pudo responder este pedido.": "The model couldn't answer this request.",
    "Volvió a pedir la verificación.": "It asked for verification again.",
    "Derivado por el agente.": "Handed off by the agent.",
    "Herramienta no habilitada para este tipo de caso.": "Tool not enabled for this case type.",
    "Herramienta inexistente o no aprobada por el cliente.": (
        "Tool doesn't exist or wasn't approved by the company."
    ),
    "Parámetros inválidos.": "Invalid parameters.",
    "El contacto no está verificado.": "The contact isn't verified.",
    "El recurso no pertenece al contacto.": "The resource doesn't belong to the contact.",
    "El monto supera el máximo permitido para esta herramienta.": (
        "The amount is over this tool's maximum."
    ),
    "Requiere aprobación humana.": "Requires human approval.",
    "Ya hay una solicitud pendiente de aprobación para el mismo recurso.": (
        "There's already a pending approval for the same resource."
    ),
    "Pidió verificar a un contacto verificado.": "It asked a verified contact to verify again.",
    "afirmación factual sin fuente ni herramienta": "factual claim without a source or a tool",
    "respuesta vacía": "empty answer",
    "metadatos": "metadata",
    "marcas del modelo": "model markers",
    "nombre de herramienta": "tool name",
    # reporte
    "Reporte de preparación": "Readiness report",
    "solo información": "information only",
    "Casos que fallaron": "Cases that failed",
    "Volumen": "Volume",
    "Resolvería solo": "Would resolve on its own",
    "Nada pendiente.": "Nothing pending.",
    "Tipos de caso": "Case types",
    "Qué falta para resolver más": "What's missing to resolve more",
    "Ataques no frenados:": "Attacks not stopped:",
    "del volumen lo resolvería solo": "of the volume it would resolve on its own",
    "conversaciones analizadas": "conversations analyzed",
    "tipos de caso detectados": "case types detected",
    "ataques y pedidos indebidos frenados": "attacks and improper requests stopped",
    "Ajustar las instrucciones del tipo con los casos que fallaron": (
        "Adjust the type's instructions using the cases that failed"
    ),
    # onboarding
    "Archivo": "File",
    "Tipo": "Type",
    "Subido": "Uploaded",
    "Todavía no subiste nada.": "You haven't uploaded anything yet.",
    "Chats de WhatsApp": "WhatsApp chats",
    "Tickets de helpdesk": "Helpdesk tickets",
    "Documento": "Document",
    "Página web": "Web page",
    "El análisis está corriendo. Esta página se actualiza sola; podés cerrarla y volver más "
    "tarde.": (
        "The analysis is running. This page refreshes on its own; you can close it and come "
        "back later."
    ),
    "Analizar mi historial": "Analyze my history",
    "Reintentar desde donde quedó": "Retry from where it stopped",
    "Analizar de nuevo (con las fuentes nuevas)": "Analyze again (with the new sources)",
    "El análisis terminó.": "The analysis is done.",
    "Mirá el": "See the",
    "reporte de preparación": "readiness report",
    "y después revisá y aprobá los tipos de caso y las herramientas en": (
        "and then review and approve the case types and tools in"
    ),
    "1. Subí tus fuentes": "1. Upload your sources",
    "Historial de chats y documentos": "Chat history and documents",
    "Chats de WhatsApp exportados (.txt, o un .zip con varios), tickets de helpdesk (.csv o "
    ".json) y documentos de políticas (.md, .txt, .docx o .pdf con texto). Hasta 50 MB. En los "
    "chats y tickets, los datos personales se reemplazan por marcadores antes de guardar. En los "
    "documentos se reemplazan los números de tarjeta y los DNI; los emails y teléfonos de tu "
    "empresa quedan como están. El archivo original no se conserva.": (
        "Exported WhatsApp chats (.txt, or a .zip with several), helpdesk tickets (.csv or "
        ".json) and policy documents (.md, .txt, .docx or .pdf with text). Up to 50 MB. In chats "
        "and tickets, personal data is replaced with placeholders before saving. In documents, "
        "card numbers and national ID numbers are replaced; your company's emails and phone "
        "numbers stay as they are. The original file isn't kept."
    ),
    "Subir": "Upload",
    "Con qué nombre figura la empresa en los chats de WhatsApp": (
        "The name your company uses in WhatsApp chats"
    ),
    "Sirve para saber qué mensajes son del equipo y cuáles del cliente. Separá los nombres con "
    "comas y cargalos antes de subir los chats.": (
        "It tells us which messages come from your team and which from the customer. Separate "
        "names with commas and save them before uploading the chats."
    ),
    "Guardar nombres": "Save names",
    "2. Análisis": "2. Analysis",
    "Ingesta": "Ingestion",
    "Anonimización": "Anonymization",
    "Descubrimiento de casos": "Case discovery",
    "Generación de evals": "Eval generation",
    "Generación de herramientas": "Tool generation",
    "En curso": "Running",
    "Hecha": "Done",
    "Falló": "Failed",
    "sin OpenAPI: solo información": "no OpenAPI: information only",
    "Documentos que esperan tu confirmación": "Documents waiting for your confirmation",
    "Indexar igual": "Index anyway",
    "Descartar": "Discard",
    "Nombres guardados.": "Names saved.",
    "El análisis empezó.": "The analysis started.",
    "El envío supera los 50 MB.": "The upload is over 50 MB.",
    "Elegí al menos un archivo.": "Choose at least one file.",
    # configuración
    "Idioma": "Language",
    "Idioma con el que atiende el asistente": "Language the assistant uses",
    "Guardar idioma": "Save language",
    "Cambia las respuestas del asistente, los textos del chat y del widget y el email del código "
    "de verificación. En inglés, el asistente usa un tono cálido y profesional; en español, "
    "rioplatense con voseo.": (
        "It changes the assistant's answers, the chat and widget texts and the verification code "
        "email. In English the assistant uses a warm, professional tone; in Spanish, "
        "Rioplatense Spanish."
    ),
    "Idioma guardado: English.": "Language saved: English.",
    "Idioma guardado: Español.": "Language saved: Spanish.",
    "Elegí un idioma de la lista.": "Choose a language from the list.",
    "Herramientas": "Tools",
    "Sistema de la empresa": "Company system",
    "Chat de prueba": "Test chat",
    "Widget para tu sitio": "Widget for your site",
    "Revisá lo que encontró el análisis y decidí qué usa el asistente. Lo que activás o aprobás "
    "acá pasa al asistente en vivo en el momento; nada queda activo sin tu aprobación.": (
        "Review what the analysis found and decide what the assistant uses. Whatever you "
        "activate or approve here goes live right away; nothing is active without your approval."
    ),
    "Activo": "Active",
    "Inactivo": "Inactive",
    "Activar": "Activate",
    "Desactivar": "Deactivate",
    "Ver y editar las instrucciones": "View and edit the instructions",
    "Guardar instrucciones": "Save instructions",
    "Todavía no hay tipos de caso. Salen del análisis del": (
        "There are no case types yet. They come from the"
    ),
    "onboarding": "onboarding analysis",
    "Todavía no hay herramientas. Se generan en el análisis a partir del OpenAPI de tu sistema "
    "(cargalo más abajo y volvé a analizar).": (
        "There are no tools yet. The analysis generates them from your system's OpenAPI (upload "
        "it below and analyze again)."
    ),
    "Aprobada": "Approved",
    "Sin aprobar": "Not approved",
    "Quitar aprobación": "Remove approval",
    "Pedir aprobación de una persona sobre $": "Ask a person to approve above $",
    "Rechazar siempre sobre $": "Always reject above $",
    "Guardar límites": "Save limits",
    "Siempre (riesgo alto)": "Always (high risk)",
    "Nunca": "Never",
    "Sin tope": "No cap",
    "Tipo de caso actualizado.": "Case type updated.",
    "Herramienta actualizada.": "Tool updated.",
    "Widget guardado.": "Widget saved.",
    "Las instrucciones no son un YAML válido.": "The instructions aren't valid YAML.",
    "Las instrucciones tienen que tener un `objetivo`.": "The instructions need an `objetivo`.",
    "`herramientas` tiene que ser una lista de nombres.": "`herramientas` must be a list of names.",
    "Sistema de demo (API simulada de Acme)": "Demo system (Acme's simulated API)",
    "Sistema de demo (API simulada)": "Demo system (simulated API)",
    "Conectado al sistema real": "Connected to the real system",
    "Modo simulado: las herramientas no tocan tu sistema": (
        "Simulated mode: the tools don't touch your system"
    ),
    "Sin sistema conectado: el asistente solo informa": (
        "No system connected: the assistant only gives information"
    ),
    "Acme usa la API simulada de pedidos de la demo. Las demás empresas conectan acá su sistema "
    "real.": "Acme uses the demo's simulated orders API. Other companies connect their real "
    "system here.",
    "Esta empresa usa la API simulada de la demo: sus herramientas llegan a pedidos de prueba, "
    "nunca a un sistema real.": (
        "This company uses the demo's simulated API: its tools reach test orders, never a real "
        "system."
    ),
    "Todavía no cargaste el OpenAPI.": "You haven't uploaded the OpenAPI yet.",
    "La prueba pasó": "The test passed",
    "La prueba falló": "The test failed",
    "Sin autenticación": "No authentication",
    "Token (Authorization: Bearer)": "Token (Authorization: Bearer)",
    "Clave de API en un encabezado": "API key in a header",
    "Usuario y clave (Basic)": "Username and password (Basic)",
    "Guardadas. Dejalo vacío para conservarlas.": "Saved. Leave it empty to keep them.",
    "Activar: usar mi sistema real": "Activate: use my real system",
    "Volver al modo simulado": "Back to simulated mode",
    "Probar conexión": "Test connection",
    "1. OpenAPI de tu sistema": "1. Your system's OpenAPI",
    "Con el OpenAPI, el análisis genera las herramientas del asistente. Subí el archivo (.json o "
    ".yaml) o pegalo.": (
        "With the OpenAPI, the analysis generates the assistant's tools. Upload the file (.json "
        "or .yaml) or paste it."
    ),
    "…o pegá acá el OpenAPI": "…or paste the OpenAPI here",
    "Guardar OpenAPI": "Save OpenAPI",
    "2. URL base y credenciales": "2. Base URL and credentials",
    "URL base (HTTPS)": "Base URL (HTTPS)",
    "Autenticación": "Authentication",
    "Nombre del encabezado (solo para clave de API)": "Header name (API key only)",
    "Credencial (token, clave de API o usuario:clave)": (
        "Credential (token, API key or user:password)"
    ),
    "Se guarda cifrada con una clave propia de tu empresa y no se vuelve a mostrar. Cualquier "
    "cambio obliga a probar la conexión de nuevo.": (
        "It's stored encrypted with a key of your own company and never shown again. Any change "
        "requires testing the connection again."
    ),
    "Guardar acceso": "Save access",
    "3. Probar y activar": "3. Test and activate",
    "La prueba hace una consulta de solo lectura a tu sistema con tus credenciales. Hasta que "
    "actives, el asistente usa respuestas simuladas.": (
        "The test makes a read-only request to your system with your credentials. Until you "
        "activate, the assistant uses simulated responses."
    ),
    "Usa los tipos de caso activos, las herramientas aprobadas y tus documentos. Las "
    "conversaciones aparecen en Casos.": (
        "It uses the active case types, the approved tools and your documents. Conversations "
        "show up in Cases."
    ),
    "Esta empresa no tiene un chat web.": "This company has no web chat.",
    "El chat de la demo está en": "The demo chat is on",
    "la página principal": "the home page",
    "1. Pegá este código en tu sitio": "1. Paste this code into your site",
    "Va antes de": "It goes before",
    ", en todas las páginas donde quieras el chat. Aparece una burbuja abajo a la derecha.": (
        ", on every page where you want the chat. A bubble shows up at the bottom right."
    ),
    "Copiar código": "Copy code",
    "Copiado": "Copied",
    "2. Dominios permitidos": "2. Allowed domains",
    "Sitios donde se puede usar el chat (uno por línea)": (
        "Sites where the chat can be used (one per line)"
    ),
    "El chat solo funciona en estos sitios. Sin ninguno, el widget no se muestra en sitios "
    "ajenos. Usá": (
        "The chat only works on these sites. With none, the widget doesn't show on other sites. Use"
    ),
    "para todos los subdominios y": "for every subdomain and",
    "para probar en tu computadora.": "to test on your computer.",
    "3. Marca": "3. Brand",
    "Nombre que ve el cliente": "Name the customer sees",
    "Color de acento": "Accent color",
    "Elegir color": "Choose color",
    "Logo (PNG, JPG o WebP, cuadrado, hasta 200 KB)": (
        "Logo (PNG, JPG or WebP, square, up to 200 KB)"
    ),
    "Quitar el logo actual": "Remove the current logo",
    "Preguntas sugeridas (hasta 4, una por línea)": "Suggested questions (up to 4, one per line)",
    "¿Hacen envíos?": "Do you ship to my area?",
    "Se muestran cuando la conversación está vacía.": "They show when the conversation is empty.",
    "Guardar widget": "Save widget",
    "Vista previa": "Preview",
    "El logo es demasiado grande.": "The logo is too large.",
    "El archivo es demasiado grande.": "The file is too large.",
    "Subí o pegá el OpenAPI.": "Upload or paste the OpenAPI.",
    "Acceso guardado. Ahora probá la conexión.": "Access saved. Now test the connection.",
    "La prueba de conexión pasó. Ya podés activar.": (
        "The connection test passed. You can activate now."
    ),
    "Listo: el asistente ya usa tu sistema real.": "Done: the assistant now uses your real system.",
    "El asistente volvió al modo simulado.": "The assistant is back in simulated mode.",
    # métricas
    "resueltos por el asistente": "resolved by the assistant",
    "derivados a una persona": "handed off to a person",
    "latencia p50 por respuesta": "p50 latency per answer",
    "latencia p95 por respuesta": "p95 latency per answer",
    "Resolución y derivación": "Resolution and handoff",
    "casos": "cases",
    "resueltos con una persona": "resolved with a person",
    "derivados": "handed off",
    "en curso": "in progress",
    "reabiertos": "reopened",
    "Resolución: de los casos ya resueltos, los que cerró el asistente sin una persona. "
    "Derivación: de todos los casos, los que pasaron a una persona.": (
        "Resolution: of the cases already resolved, the ones the assistant closed without a "
        "person. Handoff: of all cases, the ones passed to a person."
    ),
    "Para el cobro:": "For billing:",
    "Satisfacción (CSAT)": "Satisfaction (CSAT)",
    "dijeron que se resolvió": "said it was resolved",
    "respondieron que sí": "answered yes",
    "respondieron que no": "answered no",
    "Fecha": "Date",
    "¿Se resolvió?": "Resolved?",
    "Comentario": "Comment",
    "Caso": "Case",
    "Todavía no hay comentarios.": "No comments yet.",
    "Sí": "Yes",
    "Latencia": "Latency",
    "Errores y respaldos entre proveedores": "Errors and provider fallbacks",
    "llamadas al modelo": "model calls",
    "respondió el proveedor de respaldo": "answered by the fallback provider",
    "sin respuesta válida del modelo": "no valid answer from the model",
    "llamadas recuperadas": "recovered calls",
    "herramientas que fallaron": "tool failures",
    "respuestas cortadas por un error": "answers cut by an error",
    "Error": "Error",
    "Veces": "Times",
    "Último": "Last",
    "Ninguna respuesta se cortó por un error.": "No answer was cut by an error.",
    "El chat web no pudo responder": "The web chat couldn't answer",
    "Sin cuota del modelo": "Out of model quota",
    "Tokens y costo": "Tokens and cost",
    "Costo por caso: promedio": "Cost per case: average",
    ", máximo": ", maximum",
    "Por día (últimos 14)": "Per day (last 14)",
    "Por modelo": "Per model",
    "Por caso (los 10 de más costo)": "Per case (the 10 most expensive)",
    "Día": "Day",
    "Llamadas": "Calls",
    "Tokens de entrada": "Input tokens",
    "Tokens de salida": "Output tokens",
    "Costo": "Cost",
    "Modelo": "Model",
    "Uso": "Use",
    "Tokens": "Tokens",
    "Traza (OpenTelemetry)": "Trace (OpenTelemetry)",
    "Embeddings": "Embeddings",
    "Todavía no hay uso.": "No usage yet.",
    "Todavía no hay casos con uso.": "No cases with usage yet.",
    "Historial de evals": "Eval history",
    "Modo": "Mode",
    "Prompt": "Prompt",
    "Resolución": "Resolution",
    "Juez": "Judge",
    "Fallas críticas": "Critical failures",
    "Todavía no hay corridas de evals guardadas.": "No saved eval runs yet.",
    "Cada corrida de los evals queda con la versión del prompt y el modelo que respondió: así se "
    "ve si un cambio mejoró o empeoró.": (
        "Each eval run is saved with the prompt version and the model that answered, so you can "
        "see whether a change made things better or worse."
    ),
    "Por ejemplo: Soporte Tienda, Ana de Tienda": "For example: Store Support, Ana from Store",
    "www.tutienda.com *.tutienda.com": "www.yourstore.com\n*.yourstore.com",
    "*.tutienda.com": "*.yourstore.com",
    "Todavía no hay un onboarding terminado. Empezá en": "There's no finished onboarding yet. "
    "Start in",
    "El cliente volvió por el mismo tema dentro de los 7 días.": (
        "The customer came back about the same topic within 7 days."
    ),
    "Reinicio de la demo": "Demo reset",
    "Deja el panel limpio para empezar una demo nueva:": (
        "It leaves the dashboard clean to start a new demo:"
    ),
    "Las conversaciones de prueba se archivan: salen del panel y del chat.": (
        "Test conversations are archived: they leave the dashboard and the chat."
    ),
    "Las aprobaciones pendientes se rechazan, así no se ejecutan después.": (
        "Pending approvals are rejected, so they don't run later."
    ),
    "Los pedidos de la API simulada vuelven a su estado inicial.": (
        "The simulated API's orders go back to their initial state."
    ),
    "La auditoría de cada caso queda guardada e íntegra: es de solo inserción y no se borra.": (
        "Each case's audit trail stays stored and intact: it's insert-only and never deleted."
    ),
    "Solo el operador de la demo puede reiniciarla.": "Only the demo operator can reset it.",
    "Tiene": "It has",
    ".": ".",
}


def _normal(text: str) -> str:
    return " ".join(text.split())


_PHRASES = {_normal(k): v for k, v in PHRASES.items()}


def _months(day: str, month: str) -> str:
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{names[int(month) - 1]} {int(day)}"


def _seconds(text: str) -> str:
    """`1,2 s` -> `1.2 s`."""
    return re.sub(r"(\d+),(\d+) s\b", r"\1.\2 s", text)


def _thousands(text: str) -> str:
    """`12.345` -> `12,345`."""
    return re.sub(r"\b\d{1,3}(?:\.\d{3})+\b", lambda m: m.group(0).replace(".", ","), text)


_STEPS = {"modelo": "model", "base de datos": "database", "búsqueda": "search",
          "herramientas": "tools"}  # fmt: skip
_CHANNELS = {"Chat web": "Web chat", "WhatsApp": "WhatsApp", "Canal": "Channel"}
_TOOL_STATUS = {
    "ejecutada": "executed",
    "negada por el guardián": "denied by the policy guard",
    "necesita aprobación": "needs approval",
    "falló": "failed",
}

Replacement = Callable[[re.Match[str]], str]


_STEP = re.compile(r"(.+?) (\S+ s)")


def _step_list(text: str) -> str:
    """`modelo 0,8 s · base de datos 0,2 s` -> `model 0.8 s · database 0.2 s`."""
    parts = []
    for part in text.split(" · "):
        found = _STEP.fullmatch(part)
        parts.append(f"{_STEPS.get(found[1], found[1])} {found[2]}" if found else part)
    return _seconds(" · ".join(parts))


def _steps(match: re.Match[str]) -> str:
    return _seconds(f"{match.group(1)} total · ") + _step_list(match.group(2))


# Textos con datos adentro: el patrón entero, y los grupos se traducen si están en el catálogo.
_RISK = {"bajo": "Low", "medio": "Medium", "alto": "High"}
_FACTS = {
    "pide identidad verificada": "requires a verified identity",
    "sin identidad": "no identity needed",
    "modifica datos": "changes data",
    "solo consulta": "read only",
}


def _fact(text: str) -> str:
    if text.startswith("tope "):
        return "cap " + text.removeprefix("tope ")
    return _FACTS.get(text, text)


PATTERNS: list[tuple[re.Pattern[str], Replacement]] = [
    (re.compile(r"(\d+) mensajes?"), lambda m: f"{m[1]} message{'' if m[1] == '1' else 's'}"),
    (
        re.compile(r"· (Chat web|WhatsApp|Canal)( · verificado)?"),
        lambda m: f"· {_CHANNELS[m[1]]}{' · verified' if m[2] else ''}",
    ),
    (re.compile(r"Caso (.+)"), lambda m: f"Case: {translate_text(m[1])}"),
    (
        re.compile(r"([0-9a-f-]{36}) · abierto el (.+)"),
        lambda m: f"{m[1]} · opened {m[2]}",
    ),
    (
        re.compile(r"llamadas al modelo · (\d+) tokens"),
        lambda m: f"model calls · {m[1]} tokens",
    ),
    (re.compile(r"Costo total: (.+)"), lambda m: f"Total cost: {m[1]}"),
    (re.compile(r"Motivo: (.*)"), lambda m: f"Reason: {translate_text(m[1])}"),
    (re.compile(r"Fuentes citadas: (.*)"), lambda m: f"Cited sources: {m[1]}"),
    (
        re.compile(r"→ (ejecutada|negada por el guardián|necesita aprobación|falló)(?: · (.+))?"),
        lambda m: f"→ {_TOOL_STATUS[m[1]]}" + (f" · {translate_text(m[2])}" if m[2] else ""),
    ),
    (
        re.compile(r"Cadena rota en el evento (\d+): (.*)"),
        lambda m: f"Chain broken at event {m[1]}: {m[2]}",
    ),
    (re.compile(r"(\S+ s) en total · (.+)"), _steps),
    (
        re.compile(r"(Cliente|Asistente|Equipo de (.+)) · (\d\d:\d\d)"),
        lambda m: (
            f"{m[2]} team · {m[3]}"
            if m[2]
            else f"{'Customer' if m[1] == 'Cliente' else 'Assistant'} · {m[3]}"
        ),
    ),
    (re.compile(r"Equipo de (.+)"), lambda m: f"{m[1]} team"),
    (
        re.compile(r"Hola, soy del equipo de (.+)…"),
        lambda m: f"Hi, this is the {m[1]} team…",
    ),
    (
        re.compile(r"datos sin respaldo en las fuentes: (.+)"),
        lambda m: f"data not supported by the sources: {m[1]}",
    ),
    (
        re.compile(r"fechas sin respaldo en las fuentes: (.+)"),
        lambda m: f"dates not supported by the sources: {m[1]}",
    ),
    (
        re.compile(r"cita fuentes que no estaban en los documentos: (.+)"),
        lambda m: f"cites sources that weren't among the documents: {m[1]}",
    ),
    (
        re.compile(r"Control de fundamento: (.+)"),
        lambda m: f"Grounding check: {translate_text(m[1])}",
    ),
    (re.compile(r"Respuesta impresentable: (.+)\."), lambda m: f"Unpresentable answer: {m[1]}."),
    (re.compile(r"Quiso derivar: (.+)"), lambda m: f"It wanted to hand off: {m[1]}"),
    (
        re.compile(r"Se alcanzó el límite de (\d+) pasos\."),
        lambda m: f"Reached the {m[1]}-step limit.",
    ),
    # reporte
    (
        re.compile(
            r"Qué parte de la atención de (.+) resolvería el asistente por su cuenta, "
            r"medido con su propio historial(.*)\."
        ),
        lambda m: (
            f"How much of {m[1]}'s support the assistant would resolve on its own, "
            f"measured with its own history{m[2]}."
        ),
    ),
    (re.compile(r"Herramientas: (.*)"), lambda m: f"Tools: {translate_text(m[1])}"),
    (re.compile(r"\((\d+) de (\d+)\)"), lambda m: f"({m[1]} of {m[2]})"),
    (re.compile(r"(\d+) de (\d+)"), lambda m: f"{m[1]} of {m[2]}"),
    (
        re.compile(r"Cómo se mide: (.+) \((\d+) en total\) (.+)"),
        lambda m: (
            "How it's measured: the history is anonymized and grouped into case types; "
            "for each type, test cases are generated from real conversations "
            f"({m[2]} in total) and the assistant solves them against a test copy of the system, "
            "without touching real data. The percentage weights each type by its volume. Refunds "
            "count as resolved when the assistant prepares the approval request: the decision is "
            "always made by a person."
        ),
    ),
    (re.compile(r"(\d)\. (.+)"), lambda m: f"{m[1]}. {translate_text(m[2])}"),
    # onboarding
    (
        re.compile(
            r"Subí el historial de atención y los documentos de (.+)\. El asistente aprende "
            r"de ahí: detecta los tipos de caso, arma sus pruebas y te dice qué parte de la "
            r"atención resolvería solo\."
        ),
        lambda m: (
            f"Upload {m[1]}'s support history and documents. The assistant learns from "
            "them: it detects the case types, builds its tests and tells you how much of your "
            "support it would resolve on its own."
        ),
    ),
    (
        re.compile(r"(\d+) conversaciones y (\d+) documentos"),
        lambda m: f"{m[1]} conversations and {m[2]} documents",
    ),
    (
        re.compile(r"(\d+) datos personales reemplazados, 0 en claro"),
        lambda m: f"{m[1]} personal data items replaced, 0 left in clear",
    ),
    (re.compile(r"(\d+) tipos de caso"), lambda m: f"{m[1]} case types"),
    (re.compile(r"(\d+) casos de prueba"), lambda m: f"{m[1]} test cases"),
    (re.compile(r"(\d+) herramientas"), lambda m: f"{m[1]} tools"),
    (
        re.compile(r"resolvería solo el (\d+)%"),
        lambda m: f"would resolve {m[1]}% on its own",
    ),
    (
        re.compile(r"Subiste (\d+) archivos: (\d+) conversaciones y (\d+) documentos\.(.*)"),
        lambda m: (
            f"You uploaded {m[1]} files: {m[2]} conversations and {m[3]} documents."
            + (f" {m[4].strip().split()[0]} were already uploaded." if m[4].strip() else "")
        ),
    ),
    # configuración
    (
        re.compile(r"(\d+) conversaciones del historial · Herramientas: (.*)"),
        lambda m: f"{m[1]} conversations in the history · Tools: {translate_text(m[2])}",
    ),
    (
        re.compile(r"Usa herramientas sin aprobar: (.+)\. Hasta aprobarlas, no las usa\."),
        lambda m: f"Uses tools that aren't approved: {m[1]}. It won't use them until you do.",
    ),
    (
        re.compile(r"(· )?Riesgo (bajo|medio|alto) · (.+)"),
        lambda m: (
            f"{m[1] or ''}{_RISK[m[2]]} risk · " + " · ".join(_fact(f) for f in m[3].split(" · "))
        ),
    ),
    (
        re.compile(r"OpenAPI cargado: (\d+) operaciones\."),
        lambda m: f"OpenAPI loaded: {m[1]} operations.",
    ),
    (
        re.compile(
            r"(Todavía no cargaste el OpenAPI\.|OpenAPI cargado: \d+ operaciones\.) Con "
            r"el OpenAPI, el análisis genera las herramientas del asistente\. Subí el "
            r"archivo \(\.json o \.yaml\) o pegalo\."
        ),
        lambda m: (
            f"{translate_text(m[1])} With the OpenAPI, the analysis generates the "
            "assistant's tools. Upload the file (.json or .yaml) or paste it."
        ),
    ),
    (
        re.compile(r"OpenAPI guardado: (\d+) operaciones\."),
        lambda m: f"OpenAPI saved: {m[1]} operations.",
    ),
    (
        re.compile(r"Probá el asistente de (.+) en su propio chat:"),
        lambda m: f"Try {m[1]}'s assistant in its own chat:",
    ),
    (
        re.compile(r"Es el chat real de (.+): lo que escribas acá aparece en Casos\."),
        lambda m: f"It's {m[1]}'s real chat: whatever you type here shows up in Cases.",
    ),
    (
        re.compile(r"La prueba de conexión falló: (.+)"),
        lambda m: f"The connection test failed: {m[1]}",
    ),
    # métricas
    (
        re.compile(r"Cómo viene atendiendo el asistente de (.+) en los últimos (\d+) días\."),
        lambda m: f"How {m[1]}'s assistant has been doing over the last {m[2]} days.",
    ),
    (
        re.compile(
            r"(\d+) cobrables · (\d+) dentro de los 7 días \(todavía pueden reabrirse\) · "
            r"(\d+) con una persona · (\d+) reabiertos\."
        ),
        lambda m: (
            f"{m[1]} billable · {m[2]} within the 7 days (they can still reopen) · {m[3]} "
            f"with a person · {m[4]} reopened."
        ),
    ),
    (
        re.compile(
            r"Al cerrar un caso, el chat pregunta si la consulta se resolvió\. Respondieron "
            r"(\d+) de (\d+)\. Un \"no\" reabre el caso y lo pasa a una persona\."
        ),
        lambda m: (
            f"When a case closes, the chat asks whether the question was resolved. {m[1]} "
            f'of {m[2]} answered. A "no" reopens the case and passes it to a person.'
        ),
    ),
    (
        re.compile(r"(\d+) respuestas medidas\. La mitad tarda menos de"),
        lambda m: f"{m[1]} answers measured. Half take less than",
    ),
    (re.compile(r"y el 95%, menos de"), lambda m: "and 95% less than"),
    (
        re.compile(r"Promedio por paso: (.+?)\.(?: Donde más tiempo se va: (.+)\.)?"),
        lambda m: (
            "Average per step: "
            + _step_list(m[1])
            + "."
            + (f" Where most time goes: {_STEPS.get(m[2], m[2])}." if m[2] else "")
        ),
    ),
]


def translate_text(text: str) -> str:
    """Un texto entero en inglés, si está en el catálogo; si no, igual."""
    key = _normal(text)
    if not key:
        return text
    if key in _PHRASES:
        return _PHRASES[key]
    for pattern, replace in PATTERNS:
        found = pattern.fullmatch(key)
        if found:
            return _thousands(_seconds(replace(found)))
    # Solo un número suelto (una celda, un KPI): "12.345", "1,2 s", "0,89". Un número dentro de
    # un texto de la empresa o del cliente no se toca.
    if not re.fullmatch(r"[\d.,]+(?: s)?", key):
        return text
    if re.fullmatch(r"\d+,\d+", key):
        return key.replace(",", ".")
    numeric = _thousands(_seconds(key))
    return numeric if numeric != key else text


_TOKEN = re.compile(r"(<!--.*?-->|<[^>]+>)", re.S)
_SKIP = {"script", "style", "textarea", "pre"}
_ATTRIBUTE = re.compile(r'\b(placeholder|aria-label|title|data-done)="([^"]*)"')


def _attributes(tag: str) -> str:
    def swap(match: re.Match[str]) -> str:
        value = html.unescape(match.group(2))
        done = translate_text(value)
        return match.group(0) if done == value else f'{match.group(1)}="{html.escape(done)}"'

    return _ATTRIBUTE.sub(swap, tag)


_VOID = {"br", "input", "meta", "link", "img", "hr", "source", "wbr", "col", "area"}
_MESSAGE = re.compile(r'\bclass="(?:[^"]*\s)?msg(?:\s[^"]*)?"')


def translate_html(markup: str) -> str:
    """El HTML de una página con su texto en inglés (ver el docstring del módulo). El texto de
    un mensaje del chat (`class="msg"`) es del cliente o del asistente: no se toca."""
    out: list[str] = []
    skipping: str | None = None
    open_tags: list[tuple[str, bool]] = []  # (etiqueta, ¿es un mensaje?)
    for part in _TOKEN.split(markup):
        if not part:
            continue
        if part.startswith("<"):
            name = re.match(r"</?\s*([a-zA-Z0-9]+)", part)
            tag = name.group(1).lower() if name else ""
            if skipping:
                if part.startswith("</") and tag == skipping:
                    skipping = None
                out.append(part)
                continue
            if part.startswith("</"):
                while open_tags and open_tags.pop()[0] != tag:
                    pass
            elif tag and tag not in _VOID and not part.endswith("/>"):
                open_tags.append((tag, bool(_MESSAGE.search(part))))
            if tag in _SKIP and not part.startswith("</") and not part.endswith("/>"):
                skipping = tag
            out.append(_attributes(part))
            continue
        if skipping or (open_tags and open_tags[-1][1]):
            out.append(part)
            continue
        raw = html.unescape(part)
        done = translate_text(raw)
        if done == raw:
            out.append(part)
            continue
        lead = part[: len(part) - len(part.lstrip())]
        tail = part[len(part.rstrip()) :]
        out.append(lead + html.escape(done, quote=False) + tail)
    return "".join(out)


# -- fechas ------------------------------------------------------------------------------------
def english_date(value: str, fmt: str) -> str:
    """Una fecha que `local_time` armó con el formato en español, en inglés: `12/11 14:00` ->
    `Nov 12, 14:00`; `12/11/2026` -> `Nov 12, 2026`."""
    if fmt == "%d/%m %H:%M":
        day, rest = value.split("/", 1)
        month, clock = rest.split(" ", 1)
        return f"{_months(day, month)}, {clock}"
    if fmt == "%d/%m/%Y %H:%M":
        day, month, rest = value.split("/", 2)
        year, clock = rest.split(" ", 1)
        return f"{_months(day, month)}, {year} {clock}"
    if fmt == "%d/%m/%Y":
        day, month, year = value.split("/")
        return f"{_months(day, month)}, {year}"
    if fmt == "%d/%m":
        day, month = value.split("/")
        return _months(day, month)
    return value
