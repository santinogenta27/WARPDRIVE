"""Panel en inglés (Hito 8, tarea 2; decisión 037).

El idioma del panel es el de la empresa de la sesión (`tenants.settings.language`). `enter` lo
fija en `LANGUAGE` al principio de cada pedido. Con `en`:

- Las ayudas comunes (estados, montos, fechas, temas, acciones) ya salen en inglés: ver
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
# Tema del panel del usuario (decisión 085): claro por defecto, oscuro si lo eligió.
THEME: ContextVar[str] = ContextVar("panel_theme", default="light")


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
    "Ese tema no existe.": "That topic doesn't exist.",
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
    # registro e invitación (decisión 058; en inglés desde el Hito 13)
    "Creá la cuenta de tu empresa": "Create your company's account",
    "Crear cuenta": "Create account",
    "Un agente que atiende a tus clientes con tus políticas y tu sistema.": (
        "An AI agent that serves your customers with your policies and your store."
    ),
    "Tu piloto conserva las políticas y los productos que cargamos en la prueba.": (
        "Your pilot keeps the policies and products we loaded in the trial."
    ),
    "Nombre de la empresa": "Company name",
    "Tu email": "Your email",
    "Te mandamos un enlace para confirmar tu email. ¿Ya tenés cuenta?": (
        "We'll send you a link to confirm your email. Already have an account?"
    ),
    "Ingresá": "Sign in",
    "Revisá tu email": "Check your email",
    "Te mandamos un enlace para confirmar tu email y crear la cuenta. Vence en 24 horas.": (
        "We sent you a link to confirm your email and create the account. It expires in 24 hours."
    ),
    "Si no llega en unos minutos, revisá el correo no deseado.": (
        "If it doesn't arrive in a few minutes, check your spam folder."
    ),
    "Elegí tu clave": "Choose your password",
    "Entrar al panel": "Go to the dashboard",
    "La prueba ya no está disponible. Registrate sin ella.": (
        "The trial isn't available anymore. Sign up without it."
    ),
    "Usá el email con el que hiciste la prueba.": "Use the email you used for the trial.",
    "El nombre de la empresa tiene que tener entre 2 y 80 caracteres.": (
        "The company name must be between 2 and 80 characters."
    ),
    "El email no es válido.": "That email isn't valid.",
    "El email del usuario no es válido.": "That email isn't valid.",
    "Demasiados registros con ese email. Probá de nuevo mañana.": (
        "Too many sign-ups with that email. Please try again tomorrow."
    ),
    "El enlace no es válido o venció. Registrate de nuevo.": (
        "The link isn't valid or has expired. Please sign up again."
    ),
    "Ya hay una empresa con ese nombre.": "There's already a company with that name.",
    "Ese email ya tiene un usuario.": "That email already has an account.",
    "La invitación no es válida, ya se usó o venció.": (
        "The invitation isn't valid, was already used or has expired."
    ),
    # menú
    "Casos": "Cases",
    "Aprobaciones": "Approvals",
    "Onboarding": "Onboarding",
    "Conocimiento": "Knowledge",
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
    # confirmación después de decidir (demo en inglés, cambio D)
    "Reembolso aprobado · se envió el email de confirmación": (
        "Refund approved · confirmation email sent"
    ),
    "Reembolso aprobado · no se pudo enviar el email de confirmación": (
        "Refund approved · the confirmation email couldn't be sent"
    ),
    "Reembolso aprobado": "Refund approved",
    "Solicitud aprobada": "Request approved",
    "Aprobada, pero el sistema de la empresa no pudo completarla": (
        "Approved, but the company's system couldn't complete it"
    ),
    "Solicitud rechazada": "Request rejected",
    "No hay pedidos esperando aprobación.": "Nothing is waiting for approval.",
    "Las acciones de riesgo alto (como un reembolso) no se ejecutan hasta que una persona las "
    "aprueba. Al aprobar, se ejecuta y el cliente recibe el aviso en el chat.": (
        "High-risk actions (like a refund) don't run until a person approves them. When you "
        "approve, the action runs and the customer is notified in the chat."
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
    "Usó una acción": "Used an action",
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
    "Envió el email de confirmación del reembolso": "Sent the refund confirmation email",
    "No se pudo enviar el email de confirmación del reembolso": (
        "The refund confirmation email couldn't be sent"
    ),
    "Referencia": "Reference",
    "Caso resuelto": "Case resolved",
    "Caso resuelto por el agente": "Case resolved by the agent",
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
    "El tema no está activo para la empresa.": ("The topic isn't active for the company."),
    "El cliente amenaza con una denuncia o acción legal.": (
        "The customer is threatening a complaint or legal action."
    ),
    "El cliente respondió que su consulta no se resolvió.": (
        "The customer replied that their question wasn't resolved."
    ),
    "El modelo no pudo responder este pedido.": "The model couldn't answer this request.",
    "Volvió a pedir la verificación.": "It asked for verification again.",
    "Derivado por el agente.": "Handed off by the agent.",
    "Acción no habilitada para este tema.": "Action not enabled for this topic.",
    "Acción inexistente o no aprobada por el cliente.": (
        "Action doesn't exist or wasn't approved by the company."
    ),
    "Parámetros inválidos.": "Invalid parameters.",
    "El contacto no está verificado.": "The contact isn't verified.",
    "El recurso no pertenece al contacto.": "The resource doesn't belong to the contact.",
    "El monto supera el máximo permitido para esta acción.": (
        "The amount is over this action's maximum."
    ),
    "Requiere aprobación humana.": "Requires human approval.",
    "Ya hay una solicitud pendiente de aprobación para el mismo recurso.": (
        "There's already a pending approval for the same resource."
    ),
    # modelo y gasto (decisión 049)
    "Modelo y gasto": "Model and spending",
    "Key en uso:": "Key in use:",
    "La de la plataforma, con límites": "The platform's, with limits",
    "Gasto del mes:": "This month's spending:",
    "Tope:": "Cap:",
    "Llamadas al modelo:": "Model calls:",
    "Al 80 % se envía un aviso; al 100 %, el agente pasa cada mensaje a una persona del equipo "
    "hasta el mes siguiente o hasta que subas el tope.": (
        "At 80% you get an alert; at 100%, the agent hands every message to a person on "
        "your team until next month or until you raise the cap."
    ),
    "Proveedor": "Provider",
    "Usar la de la plataforma": "Use the platform's",
    "API key (se guarda cifrada; dejala vacía para conservar la guardada)": (
        "API key (stored encrypted; leave it empty to keep the saved one)"
    ),
    "Tope mensual en USD (vacío: sin tope propio)": (
        "Monthly cap in USD (empty: no cap of your own)"
    ),
    "Guardar": "Save",
    "Modelo y tope guardados.": "Model and cap saved.",
    "El tope tiene que ser un número.": "The cap has to be a number.",
    "El tope no puede ser negativo.": "The cap can't be negative.",
    "Falta la API key de ese proveedor.": "The API key for that provider is missing.",
    "Elegí un proveedor de la lista.": "Choose a provider from the list.",
    # conectar una tienda y probar acciones (decisión 057)
    "¿Usás Shopify o Tiendanube?": "Do you use Shopify or Tiendanube?",
    "Conectá tu tienda sin OpenAPI: se crean las acciones de pedidos, envíos, reembolsos y "
    "cupones, sin aprobar. Después probá la conexión y aprobalas más arriba.": (
        "Connect your store without an OpenAPI: the order, shipping, refund and coupon actions are "
        "created, unapproved. Then test the connection and approve them above."
    ),
    "Plataforma": "Platform",
    "Tienda (Shopify: mitienda.myshopify.com · Tiendanube: el número de tu tienda)": (
        "Store (Shopify: mystore.myshopify.com · Tiendanube: your store number)"
    ),
    "Token de acceso (de una app propia de tu tienda)": (
        "Access token (from a custom app of your store)"
    ),
    "Conectar tienda": "Connect store",
    "Número de pedido de ejemplo": "Sample order number",
    "Probar acciones": "Test actions",
    "Tienda conectada. Probá la conexión y aprobá sus acciones.": (
        "Store connected. Test the connection and approve its actions."
    ),
    # equipo e invitaciones (decisión 058)
    "¿Tu empresa no tiene cuenta?": "Your company doesn't have an account?",
    "Creala acá": "Create it here",
    "Esta sección es solo para los admins de la empresa.": "This section is for admins only.",
    "Equipo": "Team",
    "Email": "Email",
    "Rol": "Role",
    "Estado": "Status",
    "Admin": "Admin",
    "Persona del equipo": "Team member",
    "Invitación enviada": "Invitation sent",
    "Invitar": "Invite",
    "Enviar invitación": "Send invitation",
    "Un admin ve y cambia todo. Una persona del equipo atiende Casos y Aprobaciones, pero no "
    "cambia la configuración.": (
        "An admin sees and changes everything. A team member handles Cases and Approvals but "
        "doesn't change the settings."
    ),
    # nombre y saludo del agente (decisión 052)
    "Nombre del agente (opcional)": "Agent's name (optional)",
    "Saludo al abrir el chat (opcional)": "Greeting when the chat opens (optional)",
    "Por ejemplo: Nina": "For example: Nina",
    "El nombre del agente tiene que tener entre 2 y 40 caracteres.": (
        "The agent's name has to be 2 to 40 characters long."
    ),
    "El saludo puede tener hasta 300 caracteres.": "The greeting can be up to 300 characters.",
    "El nombre y el saludo no pueden llevar < ni >.": "The name and greeting can't contain < or >.",
    # ventas atribuidas (decisión 063)
    "Ventas atribuidas al agente": "Sales attributed to the agent",
    "ventas": "sales",
    "monto": "amount",
    "links de compra": "checkout links",
    "de los links terminan en compra": "of the links end in a purchase",
    "Cuenta las compras que la tienda informa como completadas desde un link que armó el "
    "agente: cada link lleva el identificador de su conversación.": (
        "Counts the purchases the store reports as completed from a link the agent built: "
        "each link carries the identifier of its conversation."
    ),
    "Actualizar ventas": "Update sales",
    "Ventas actualizadas.": "Sales updated.",
    "No hay ventas nuevas.": "No new sales.",
    # ventas cruzadas (decisión 061)
    "Ventas cruzadas": "Cross-selling",
    "Producto": "Product",
    "Cuándo": "When",
    "Descuento máximo": "Maximum discount",
    "Quitar": "Remove",
    "Todavía no hay productos para ofrecer.": "No products to offer yet.",
    "Producto (con el nombre que tiene en tu tienda)": "Product (with its name in your store)",
    "Cuándo ofrecerlo": "When to offer it",
    "Descuento máximo (%)": "Maximum discount (%)",
    "Agregar": "Add",
    "No hay temas activos donde ofrecer algo.": (
        "There are no active topics where something can be offered."
    ),
    "El agente ofrece como mucho un producto por conversación, después de resolver lo que "
    "pidió el cliente, y nunca si está enojado o en medio de un reclamo. El descuento no pasa el "
    "máximo.": (
        "The agent offers at most one product per conversation, after solving what the "
        "customer asked, and never when they're upset or in the middle of a complaint. The "
        "discount never goes over the maximum."
    ),
    "Producto para ofrecer agregado.": "Product to offer added.",
    "Producto quitado.": "Product removed.",
    "El descuento tiene que ser un número.": "The discount has to be a number.",
    "El descuento máximo va de 0 a 50 %.": "The maximum discount goes from 0 to 50%.",
    "Falta el producto.": "The product is missing.",
    "Elegí al menos un tema.": "Choose at least one topic.",
    "Las ventas no se ofrecen en un reclamo: elegí otro tema.": (
        "Products aren't offered during a complaint: choose another topic."
    ),
    # reglas de atención (decisión 051)
    "Reglas de atención": "Service rules",
    "Exige identidad verificada": "Requires a verified identity",
    "Siempre pide aprobación": "Always asks for approval",
    "Guardar reglas": "Save rules",
    "Días en que atiende el equipo": "Days your team is available",
    "Lun": "Mon",
    "Mar": "Tue",
    "Mié": "Wed",
    "Jue": "Thu",
    "Vie": "Fri",
    "Sáb": "Sat",
    "Dom": "Sun",
    "Desde": "From",
    "Hasta": "To",
    "El agente atiende siempre. Fuera de este horario, cuando pasa un caso a una persona, le "
    "dice al cliente cuándo le responden. Sin días marcados, no se avisa nada.": (
        "The agent is always on. Outside these hours, when it hands a case to a person, it "
        "tells the customer when they'll get a reply. With no days checked, nothing is said."
    ),
    "Email que recibe los casos derivados y las aprobaciones": (
        "Email that receives handed-off cases and approvals"
    ),
    "Vacío: el aviso va a los usuarios de la empresa.": "Empty: the alert goes to your users.",
    "Reglas de atención guardadas.": "Service rules saved.",
    "Elegí al menos un día de atención.": "Choose at least one day.",
    "El horario va en formato HH:MM, por ejemplo 09:00.": "Hours go as HH:MM, for example 09:00.",
    "La hora de cierre tiene que ser posterior a la de apertura.": (
        "The closing time has to be after the opening time."
    ),
    "El email para derivar no es válido.": "The handoff email isn't valid.",
    # documentos indexados y sitio web (decisión 050)
    "Sitio web de la empresa": "Company website",
    "Se leen hasta 30 páginas, sin salir de tu dominio. Volver a cargarlo actualiza las páginas.": (
        "Up to 30 pages are read, without leaving your domain. Loading it again updates the pages."
    ),
    "Leer el sitio": "Read the website",
    "Documentos indexados": "Indexed documents",
    "Lo que el agente puede citar. Para actualizar uno, subí el archivo de nuevo con el mismo "
    "nombre: reemplaza al anterior.": (
        "What the agent can cite. To update one, upload the file again with the same name: "
        "it replaces the previous one."
    ),
    "Título": "Title",
    "Fragmentos": "Chunks",
    "Indexado": "Indexed",
    "Borrar": "Delete",
    "Documento borrado: el agente ya no lo usa.": (
        "Document deleted: the agent no longer uses it."
    ),
    "La dirección tiene que empezar con https://.": "The address has to start with https://.",
    # vista detrás de escena (decisión 041)
    "Vista detrás de escena": "Behind the scenes",
    "Activada": "On",
    "Desactivada": "Off",
    "Abrila en": "Open it at",
    "Una página para mostrar la demo: el sitio con el chat a la izquierda y, a la derecha, cada "
    "paso del caso en vivo (intención, verificación, acciones, controles, fuentes, "
    "aprobación, latencia y costo). Es de solo lectura y no muestra mensajes ni datos personales "
    "de los clientes.": (
        "A page to show the demo: the site with the chat on the left and, on the right, each step "
        "of the case live (intent, verification, actions, safety checks, sources, approval, "
        "latency and cost). It's read-only and doesn't show customers' messages or personal data."
    ),
    "Vista detrás de escena activada.": "Behind-the-scenes view turned on.",
    "Vista detrás de escena desactivada.": "Behind-the-scenes view turned off.",
    # conducta del agente (decisión 040)
    "Pidió empatía antes de responder": "Asked for empathy before answering",
    "Atendió un segundo tema": "Handled a second topic",
    "Cliente molesto, sin empatía.": "Upset customer, no empathy.",
    "afirma una acción que ninguna acción hizo en este turno": (
        "it states an action no action performed in this turn"
    ),
    "Hay una solicitud pendiente para el mismo recurso en este caso.": (
        "There's a pending request for the same resource in this case."
    ),
    "Cancelada por el cliente.": "Cancelled by the customer.",
    "No había nada pendiente.": "There was nothing pending.",
    "Pidió verificar a un contacto verificado.": "It asked a verified contact to verify again.",
    "Quiso derivar con los datos de una acción ya disponibles.": (
        "It tried to hand off with an action's data already in hand."
    ),
    "afirmación factual sin fuente ni acción": "factual claim without a source or an action",
    "respuesta vacía": "empty answer",
    "metadatos": "metadata",
    "marcas del modelo": "model markers",
    "nombre de acción": "action name",
    # reporte
    "Reporte de preparación": "Readiness report",
    "solo información": "information only",
    "Casos que fallaron": "Cases that failed",
    "Volumen": "Volume",
    "Resolvería solo": "Would resolve on its own",
    "Nada pendiente.": "Nothing pending.",
    "Temas": "Topics",
    "Qué falta para resolver más": "What's missing to resolve more",
    "Ataques no frenados:": "Attacks not stopped:",
    "del volumen lo resolvería solo": "of the volume it would resolve on its own",
    "conversaciones analizadas": "conversations analyzed",
    "temas detectados": "topics detected",
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
    "y después revisá y aprobá los temas y las acciones en": (
        "and then review and approve the topics and actions in"
    ),
    "1. Subí tus fuentes": "1. Upload your sources",
    "Historial de chats y documentos": "Chat history and documents",
    "Chats de WhatsApp exportados (.txt, o un .zip con varios), tickets de helpdesk (.csv o "
    ".json) y documentos de políticas (.md, .txt, .docx, .pdf con texto o una tabla .csv, como un "
    "catálogo). Hasta 50 MB. En los "
    "chats y tickets, los datos personales se reemplazan por marcadores antes de guardar. En los "
    "documentos se reemplazan los números de tarjeta y los DNI; los emails y teléfonos de tu "
    "empresa quedan como están. El archivo original no se conserva.": (
        "Exported WhatsApp chats (.txt, or a .zip with several), helpdesk tickets (.csv or "
        ".json) and policy documents (.md, .txt, .docx, .pdf with text or a .csv table, like a "
        "catalog). Up to 50 MB. In chats "
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
    "Generación de pruebas": "Test generation",
    "Generación de acciones": "Action generation",
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
    "Idioma con el que atiende el agente": "Language the agent uses",
    "Guardar idioma": "Save language",
    "Cambia las respuestas del agente, los textos del chat y del widget y el email del código "
    "de verificación. En inglés, el agente usa un tono cálido y profesional; en español, "
    "rioplatense con voseo.": (
        "It changes the agent's answers, the chat and widget texts and the verification code "
        "email. In English the agent uses a warm, professional tone; in Spanish, "
        "Rioplatense Spanish."
    ),
    "Idioma guardado: English.": "Language saved: English.",
    "Idioma guardado: Español.": "Language saved: Spanish.",
    "Elegí un idioma de la lista.": "Choose a language from the list.",
    "Acciones": "Actions",
    "Sistema de la empresa": "Company system",
    "Chat de prueba": "Test chat",
    "Widget para tu sitio": "Widget for your site",
    "Revisá lo que encontró el análisis y decidí qué usa el agente. Lo que activás o aprobás "
    "acá pasa al agente en vivo en el momento; nada queda activo sin tu aprobación.": (
        "Review what the analysis found and decide what the agent uses. Whatever you "
        "activate or approve here goes live right away; nothing is active without your approval."
    ),
    "Activo": "Active",
    "Inactivo": "Inactive",
    "Activar": "Activate",
    "Desactivar": "Deactivate",
    "Ver y editar las instrucciones": "View and edit the instructions",
    "Guardar instrucciones": "Save instructions",
    "Todavía no hay temas. Salen del análisis del": ("There are no topics yet. They come from the"),
    "onboarding": "onboarding analysis",
    "Todavía no hay acciones. Se generan en el análisis a partir del OpenAPI de tu sistema "
    "(cargalo más abajo y volvé a analizar).": (
        "There are no actions yet. The analysis generates them from your system's OpenAPI (upload "
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
    "Tema actualizado.": "Topic updated.",
    "Acción actualizada.": "Action updated.",
    "Widget guardado.": "Widget saved.",
    "Las instrucciones no son un YAML válido.": "The instructions aren't valid YAML.",
    "Las instrucciones tienen que tener un `objetivo`.": "The instructions need an `objetivo`.",
    "`acciones` tiene que ser una lista de nombres.": "`acciones` must be a list of names.",
    "Sistema de demo (API simulada de Acme)": "Demo system (Acme's simulated API)",
    "Sistema de demo (API simulada)": "Demo system (simulated API)",
    "Conectado al sistema real": "Connected to the real system",
    "Modo simulado: las acciones no tocan tu sistema": (
        "Simulated mode: the actions don't touch your system"
    ),
    "Sin sistema conectado: el agente solo informa": (
        "No system connected: the agent only gives information"
    ),
    "Acme usa la API simulada de pedidos de la demo. Las demás empresas conectan acá su sistema "
    "real.": "Acme uses the demo's simulated orders API. Other companies connect their real "
    "system here.",
    "Esta empresa usa la API simulada de la demo: sus acciones llegan a pedidos de prueba, "
    "nunca a un sistema real.": (
        "This company uses the demo's simulated API: its actions reach test orders, never a real "
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
    "Con el OpenAPI, el análisis genera las acciones del agente. Subí el archivo (.json o "
    ".yaml) o pegalo.": (
        "With the OpenAPI, the analysis generates the agent's actions. Upload the file (.json "
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
    "actives, el agente usa respuestas simuladas.": (
        "The test makes a read-only request to your system with your credentials. Until you "
        "activate, the agent uses simulated responses."
    ),
    "Usa los temas activos, las acciones aprobadas y tus documentos. Las "
    "conversaciones aparecen en la Bandeja.": (
        "It uses the active topics, the approved actions and your documents. Conversations "
        "show up in the Inbox."
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
    "Listo: el agente ya usa tu sistema real.": "Done: the agent now uses your real system.",
    "El agente volvió al modo simulado.": "The agent is back in simulated mode.",
    # métricas
    "resueltos por el agente": "resolved by the agent",
    "derivados a una persona": "handed off to a person",
    "latencia p50 por respuesta": "p50 latency per answer",
    "latencia p95 por respuesta": "p95 latency per answer",
    "Resolución y derivación": "Resolution and handoff",
    "casos": "cases",
    "resueltos con una persona": "resolved with a person",
    "derivados": "handed off",
    "en curso": "in progress",
    "reabiertos": "reopened",
    "Resolución: de los casos ya resueltos, los que cerró el agente sin una persona. "
    "Derivación: de todos los casos, los que pasaron a una persona.": (
        "Resolution: of the cases already resolved, the ones the agent closed without a "
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
    "acciones que fallaron": "action failures",
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
    "Historial de pruebas": "Test history",
    "Modo": "Mode",
    "Prompt": "Prompt",
    "Resolución": "Resolution",
    "Juez": "Judge",
    "Fallas críticas": "Critical failures",
    "Todavía no hay corridas de pruebas guardadas.": "No saved test runs yet.",
    "Cada corrida de las pruebas queda con la versión de las instrucciones y el modelo que "
    "respondió: así se ve si un cambio mejoró o empeoró.": (
        "Each test run is saved with the instructions version and the model that answered, so "
        "you can see whether a change made things better or worse."
    ),
    "Por ejemplo: Soporte Tienda, Ana de Tienda": "For example: Store Support, Ana from Store",
    "www.tutienda.com *.tutienda.com": "www.yourstore.com\n*.yourstore.com",
    "*.tutienda.com": "*.yourstore.com",
    "Todavía no hay un reporte de preparación. Empezá en": "There's no readiness report yet. "
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
    # primeros pasos (decisión 074)
    "Primeros pasos": "First steps",
    "pasos hechos": "steps done",
    "Hecho": "Done",
    "Cargá tus documentos": "Upload your documents",
    "Políticas, preguntas frecuentes o tu historial: el agente responde con ellos.": (
        "Policies, FAQs or your history: the agent answers with them."
    ),
    "Definí las reglas de atención": "Set your service rules",
    "El horario del equipo y a quién avisar cuando el agente deriva.": (
        "Your team's hours and who to notify when the agent hands off."
    ),
    "Configurá tu marca": "Set up your brand",
    "El nombre, el color y el logo del chat.": "The chat's name, color and logo.",
    "Probá el agente en el playground": "Try the agent in the playground",
    "Escribile como un cliente y mirá cada paso que da.": (
        "Write to it like a customer and watch every step it takes."
    ),
    "Instalá el widget en tu sitio": "Install the widget on your site",
    "Pegá la línea del script en tu sitio, en uno de tus dominios.": (
        "Paste the script line on your site, on one of your domains."
    ),
    "Salí en vivo": "Go live",
    "Cuando el reporte de preparación da el visto bueno, el agente atiende en tu sitio.": (
        "When the readiness report gives the go-ahead, the agent serves your site."
    ),
    # revisión de conversaciones (decisión 073)
    "Para mejorar": "To improve",
    "Marcada como correcta": "Marked as correct",
    "Marcada como incorrecta": "Marked as incorrect",
    "Revisión de la respuesta": "Review of the reply",
    "Correcta": "Correct",
    "Incorrecta": "Incorrect",
    "Motivo": "Reason",
    "Motivo (si es incorrecta)": "Reason (if incorrect)",
    "Información faltante": "Missing information",
    "Información incorrecta": "Wrong information",
    "Acción equivocada": "Wrong action",
    "Debía derivar a una persona": "Should have handed off to a person",
    "Tono": "Tone",
    "Otro": "Other",
    "Guardar revisión": "Save review",
    "Revisión guardada": "Review saved",
    "Caso de prueba creado · lo prueba el reporte de preparación": (
        "Test case created · the readiness report tests it"
    ),
    "Esa respuesta no existe.": "That reply doesn't exist.",
    (
        "Las respuestas que el equipo marcó como incorrectas. Escribí la respuesta esperada y "
        "convertila en un caso de prueba: el reporte de preparación la prueba junto con los demás."
    ): (
        "Replies the team marked as incorrect. Write the expected reply and turn it into a test "
        "case: the readiness report tests it along with the rest."
    ),
    "No hay respuestas marcadas como incorrectas.": "No replies are marked as incorrect.",
    "Respuesta esperada": "Expected reply",
    "Palabras clave que tiene que incluir (opcional, separadas por coma)": (
        "Keywords it must include (optional, comma-separated)"
    ),
    "Convertir en caso de prueba": "Turn into an test case",
    (
        "Cargá un documento que responda esta pregunta (por ejemplo, la política o la página de "
        "ayuda del tema)."
    ): (
        "Upload a document that answers this question (for example, the policy or help page on "
        "the topic)."
    ),
    "Tema:": "Topic:",
    "Cliente": "Customer",
    "Agente": "Agent",
    "La búsqueda no encontró ningún documento sobre esto.": (
        "The search didn't find any document about this."
    ),
    "Subir documentos": "Upload documents",
    # modo supervisado (decisión 072)
    "Modo supervisado": "Supervised mode",
    "Activado": "On",
    "Desactivado": "Off",
    "Modo supervisado activado.": "Supervised mode turned on.",
    "Modo supervisado desactivado.": "Supervised mode turned off.",
    (
        "Cada respuesta del agente queda como borrador en la Bandeja. Una persona del equipo la "
        "envía, la edita o la rechaza antes de que llegue al cliente, que mientras tanto ve "
        "“escribiendo…”. Las acciones de riesgo alto siguen esperando su aprobación."
    ): (
        "Every reply from the agent is saved as a draft in the Inbox. A team member sends, edits "
        "or rejects it before it reaches the customer, who sees “typing…” in the meantime. "
        "High-risk actions still wait for their approval."
    ),
    "Borradores por revisar": "Drafts to review",
    (
        "Modo supervisado: el cliente ve “escribiendo…” hasta que una persona envía o rechaza la "
        "respuesta."
    ): "Supervised mode: the customer sees “typing…” until a person sends or rejects the reply.",
    "Borrador": "Draft",
    "Canal": "Channel",
    "Respuesta del agente (podés editarla antes de enviarla)": (
        "Agent's reply (you can edit it before sending it)"
    ),
    "Respuesta enviada al cliente": "Reply sent to the customer",
    "Borrador rechazado · no se envió nada": "Draft rejected · nothing was sent",
    "Dejó la respuesta como borrador (modo supervisado)": (
        "Saved the reply as a draft (supervised mode)"
    ),
    "Una persona aprobó y envió el borrador": "A person approved and sent the draft",
    "Una persona editó y envió el borrador": "A person edited and sent the draft",
    "Una persona rechazó el borrador": "A person rejected the draft",
    "No se envió al cliente": "It wasn't sent to the customer",
    "Envió la respuesta aprobada": "Sent the approved reply",
    "Tiene": "It has",
    # Promesas cumplidas (Hito 12, decisión 084)
    "Avisos programados": "Scheduled notices",
    "El agente solo puede prometerle un aviso al cliente si hay uno de estos pendiente.": (
        "The agent can only promise the customer a notice if one of these is pending."
    ),
    "Avisarle al cliente cuando se decida la aprobación": (
        "Let the customer know when the approval is decided"
    ),
    "Responderle al cliente desde el equipo": "Reply to the customer from the team",
    "Cumplido": "Done",
    "Cancelado": "Cancelled",
    "promete un aviso a futuro sin un aviso programado que lo cumpla": (
        "it promised a future notice with no scheduled notice to keep it"
    ),
    "Respuesta frenada: prometió avisarle al cliente sin un aviso programado": (
        "Answer blocked: it promised to notify the customer and no notice was scheduled"
    ),
    # Resultados medibles (Hito 12, decisión 083)
    "Resultados": "Results",
    "Lo que resolvió el agente, medido de forma auditable, y de qué hablan tus clientes.": (
        "What the agent resolved, measured in an auditable way, and what your customers talk about."
    ),
    "Resolución autónoma": "Autonomous resolution",
    "Ver": "View",
    "Mes": "Month",
    "Exportar CSV": "Export CSV",
    "Un caso es una resolución autónoma si nunca se derivó, lo resolvió el agente, no se "
    "reabrió dentro de las 72 h y el cliente no dijo que no se resolvió. El CSV trae un renglón "
    "por caso, sin datos del cliente: con el número de caso se audita en la Bandeja.": (
        "A case is an autonomous resolution if it was never handed off, the agent resolved "
        "it, it wasn't reopened within 72 h and the customer didn't say it wasn't resolved. The "
        "CSV has one row per case, with no customer data: audit it in the Inbox with the case "
        "number."
    ),
    "resoluciones autónomas": "autonomous resolutions",
    "casos resueltos": "resolved cases",
    "tasa (sin los casos en ventana)": "rate (without the cases in the window)",
    "Análisis de conversaciones (últimos 30 días)": "Conversation analysis (last 30 days)",
    "Temas principales": "Main topics",
    "Motivos de derivación": "Handoff reasons",
    "Preguntas sin respuesta": "Unanswered questions",
    "Lo que el agente no pudo responder, agrupado por la palabra que más se repite. Sirve para "
    "saber qué documento cargar. Los ejemplos no tienen datos personales.": (
        "What the agent couldn't answer, grouped by the most repeated word. It tells you "
        "which document to upload. The examples have no personal data."
    ),
    "Otras": "Others",
    "Sin datos en estos 30 días.": "No data in these 30 days.",
    "Informe semanal": "Weekly report",
    "Cada lunes, un email a los admins con la semana anterior: conversaciones, resoluciones "
    "autónomas, derivaciones, CSAT, temas y preguntas sin respuesta.": (
        "Every Monday, an email to the admins with the previous week: conversations, autonomous "
        "resolutions, handoffs, CSAT, topics and unanswered questions."
    ),
    # Copiloto del equipo (Hito 12, decisión 082)
    "Copiloto del equipo": "Team copilot",
    "Volver a sugerir": "Suggest again",
    "Resumen": "Summary",
    "Paso siguiente": "Next step",
    "Memoria del cliente": "Customer memory",
    "ver cliente": "view customer",
    "Preferencias": "Preferences",
    "La respuesta sugerida está abajo, en “Responder como persona del equipo”: revisala y "
    "editala antes de enviarla.": (
        "The suggested reply is below, in “Reply as a team member”: check it and edit it before "
        "sending it."
    ),
    "Sin respuesta sugerida: el agente no está disponible ahora.": (
        "No suggested reply: the agent isn't available right now."
    ),
    "Revisá el motivo de la derivación y la conversación, y respondé al cliente.": (
        "Check the reason for the handoff and the conversation, and reply to the customer."
    ),
    # Voz de marca (Hito 12, decisión 081)
    "Voz de marca": "Brand voice",
    "Cálido y cercano, sin vueltas": "Warm and friendly, straight to the point",
    "Trato": "Form of address",
    "Según el idioma": "By language",
    "Vos": "Informal (vos)",
    "Tú": "Informal",
    "Usted": "Formal",
    "Palabras a usar (separadas por coma)": "Words to use (comma-separated)",
    "Palabras a evitar (separadas por coma)": "Words to avoid (comma-separated)",
    "Usar emojis": "Use emojis",
    "Firma": "Signature",
    "Va en las instrucciones del agente y la controla el supervisor de respuestas: una palabra "
    "a evitar hace que la respuesta se reescriba. La firma se agrega al final de cada "
    "respuesta y, sin emojis, se sacan antes de enviar.": (
        "It goes into the agent's instructions and the answer supervisor checks it: a word to "
        "avoid makes the answer be rewritten. The signature is added at the end of every answer "
        "and, without emojis, they're removed before sending."
    ),
    "Guardar voz de marca": "Save brand voice",
    "Voz de marca guardada.": "Brand voice saved.",
    "Elegí un trato de la lista.": "Pick an option from the list.",
    # Simulaciones (Hito 12, decisión 080)
    "Simulaciones": "Simulations",
    "Las conversaciones simuladas corren en segundo plano. Volvé a abrir el Reporte en unos "
    "minutos.": "The simulated conversations run in the background. Open the Report again in a "
    "few minutes.",
    "La última simulación falló.": "The last simulation failed.",
    "Simular 20 conversaciones": "Simulate 20 conversations",
    "clientes, 3 corridas cada uno": "customers, 3 runs each",
    "Tema": "Topic",
    "Corridas que fallaron": "Runs that failed",
    "corrida": "run",
    "Clientes simulados (enojados, confundidos o apurados) creados desde tus documentos, temas y "
    "reglas conversan con el agente. Cada conversación se repite 3 veces y un juez "
    "revisa si el cliente logró su objetivo, si se respetaron las reglas y la marca y si la "
    "respuesta fue correcta. pass^3: los clientes que pasaron las 3 veces. No toca tu sistema ni "
    "deja datos.": (
        "Simulated customers (angry, confused or in a hurry), built from your documents, "
        "topics and rules, talk with the agent. Each conversation is repeated 3 times "
        "and a judge checks whether the customer reached their goal, whether the rules and the "
        "brand were respected and whether the answer was correct. pass^3: the customers who "
        "passed all 3 times. It doesn't touch your system or leave any data."
    ),
    # Supervisor de respuestas (Hito 12, decisión 079)
    "Supervisor de respuestas": "Answer supervisor",
    "Un modelo barato revisa cada respuesta con datos, políticas o acciones antes de enviarla: "
    "que coincida con las fuentes y las acciones, con las reglas y con el tono de la marca. "
    "Puede aprobarla, pedir que se reescriba una vez o derivar a una persona.": (
        "A low-cost model reviews every answer with data, policies or actions before it's sent: "
        "that it matches the sources and the actions, the rules and the brand's tone. It can "
        "approve it, ask for one rewrite or hand off to a person."
    ),
    "Últimos 30 días": "Last 30 days",
    "Revisiones": "Reviews",
    "Pidió reescribir": "Asked for a rewrite",
    "Derivó": "Handed off",
    "Latencia media que agrega": "Average latency it adds",
    "Costo total": "Total cost",
    "Supervisor de respuestas activado.": "Answer supervisor turned on.",
    "Supervisor de respuestas desactivado.": "Answer supervisor turned off.",
    "El supervisor frenó la respuesta antes de enviarla": (
        "The supervisor stopped the answer before it was sent"
    ),
    # Memoria de los clientes (Hito 12, decisión 078)
    "Clientes": "Customers",
    "Sin nombre": "No name",
    "Compras": "Purchases",
    "Conversaciones": "Conversations",
    "Última vez": "Last seen",
    "Última vez:": "Last seen:",
    "Todavía no hay clientes verificados con memoria.": (
        "There are no verified customers with memory yet."
    ),
    "Guardar la memoria sin actividad durante": "Keep inactive memory for",
    "meses": "months",
    "Meses de retención:": "Months of retention:",
    "Retención": "Retention",
    "Lo que el agente recuerda de cada cliente verificado: nombre, conversaciones anteriores, "
    "preferencias y compras. Solo lo usa después de verificar la identidad y nunca guarda datos "
    "de pago.": (
        "What the agent remembers about each verified customer: name, earlier "
        "conversations, preferences and purchases. It only uses it after verifying their "
        "identity and never stores payment data."
    ),
    "Esta acción es solo para los admins de la empresa.": "Only the company's admins can do this.",
    "← Clientes": "← Customers",
    "Pedido": "Order",
    "Sin compras registradas.": "No purchases recorded.",
    "Conversaciones anteriores": "Earlier conversations",
    "Corregir": "Correct",
    "Nombre": "Name",
    "Preferencias (una por línea)": "Preferences (one per line)",
    "Una preferencia con datos de pago no se guarda.": (
        "A preference with payment data isn't saved."
    ),
    "Guardar cambios": "Save changes",
    "Borrar la memoria": "Delete the memory",
    "El agente deja de recordar a este cliente. Sus conversaciones siguen en la Bandeja.": (
        "The agent stops remembering this customer. Their conversations stay in the Inbox."
    ),
    "Borrar la memoria de este cliente": "Delete this customer's memory",
    "Ese cliente no existe.": "That customer doesn't exist.",
    "Memoria del cliente corregida": "Customer memory corrected",
    "Memoria del cliente borrada": "Customer memory deleted",
    "Retención guardada": "Retention saved",
    "La retención va de 1 a 36 meses": "Retention goes from 1 to 36 months",
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
          "acciones": "actions"}  # fmt: skip
_CHANNELS = {"Chat web": "Web chat", "WhatsApp": "WhatsApp", "Canal": "Channel"}
_TOOL_STATUS = {
    "ejecutada": "executed",
    "bloqueada por las reglas": "blocked by the rules",
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
    (re.compile(r"(\d+) esperando aprobación"), lambda m: f"{m[1]} waiting for approval"),
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
        re.compile(r"→ (ejecutada|bloqueada por las reglas|necesita aprobación|falló)(?: · (.+))?"),
        lambda m: f"→ {_TOOL_STATUS[m[1]]}" + (f" · {translate_text(m[2])}" if m[2] else ""),
    ),
    (
        re.compile(r"Cadena rota en el evento (\d+): (.*)"),
        lambda m: f"Chain broken at event {m[1]}: {m[2]}",
    ),
    (re.compile(r"(\S+ s) en total · (.+)"), _steps),
    (
        re.compile(r"(Cliente|Agente|Equipo de (.+)) · (\d\d:\d\d)"),
        lambda m: (
            f"{m[2]} team · {m[3]}"
            if m[2]
            else f"{'Customer' if m[1] == 'Cliente' else 'Agent'} · {m[3]}"
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
            r"Qué parte de la atención de (.+) resolvería el agente por su cuenta, "
            r"medido con su propio historial(.*)\."
        ),
        lambda m: (
            f"How much of {m[1]}'s support the agent would resolve on its own, "
            f"measured with its own history{m[2]}."
        ),
    ),
    (re.compile(r"Acciones: (.*)"), lambda m: f"Actions: {translate_text(m[1])}"),
    (re.compile(r"\((\d+) de (\d+)\)"), lambda m: f"({m[1]} of {m[2]})"),
    (re.compile(r"(\d+) de (\d+)"), lambda m: f"{m[1]} of {m[2]}"),
    (
        re.compile(r"Cómo se mide: (.+) \((\d+) en total\) (.+)"),
        lambda m: (
            "How it's measured: the history is anonymized and grouped into topics; "
            "for each type, test cases are generated from real conversations "
            f"({m[2]} in total) and the agent solves them against a test copy of the system, "
            "without touching real data. The percentage weights each type by its volume. Refunds "
            "count as resolved when the agent prepares the approval request: the decision is "
            "always made by a person."
        ),
    ),
    (re.compile(r"(\d)\. (.+)"), lambda m: f"{m[1]}. {translate_text(m[2])}"),
    # onboarding
    (
        re.compile(
            r"Subí el historial de atención y los documentos de (.+)\. El agente aprende "
            r"de ahí: detecta los temas, arma sus pruebas y te dice qué parte de la "
            r"atención resolvería solo\."
        ),
        lambda m: (
            f"Upload {m[1]}'s support history and documents. The agent learns from "
            "them: it detects the topics, builds its tests and tells you how much of your "
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
    (re.compile(r"(\d+) temas"), lambda m: f"{m[1]} topics"),
    (re.compile(r"(\d+) casos de prueba"), lambda m: f"{m[1]} test cases"),
    (re.compile(r"(\d+) acciones"), lambda m: f"{m[1]} actions"),
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
        re.compile(r"(\d+) conversaciones del historial · Acciones: (.*)"),
        lambda m: f"{m[1]} conversations in the history · Actions: {translate_text(m[2])}",
    ),
    (
        re.compile(r"Usa acciones sin aprobar: (.+)\. Hasta aprobarlas, no las usa\."),
        lambda m: f"Uses actions that aren't approved: {m[1]}. It won't use them until you do.",
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
            r"el OpenAPI, el análisis genera las acciones del agente\. Subí el "
            r"archivo \(\.json o \.yaml\) o pegalo\."
        ),
        lambda m: (
            f"{translate_text(m[1])} With the OpenAPI, the analysis generates the "
            "agent's actions. Upload the file (.json or .yaml) or paste it."
        ),
    ),
    (
        re.compile(r"OpenAPI guardado: (\d+) operaciones\."),
        lambda m: f"OpenAPI saved: {m[1]} operations.",
    ),
    (
        re.compile(r"Clave \(al menos (\d+) caracteres\)"),
        lambda m: f"Password (at least {m[1]} characters)",
    ),
    (
        re.compile(r"La clave tiene que tener al menos (\d+) caracteres\."),
        lambda m: f"The password must have at least {m[1]} characters.",
    ),
    (
        re.compile(r"Quién usa el panel de (.+)\."),
        lambda m: f"Who uses {m[1]}'s dashboard.",
    ),
    (
        re.compile(r"Probá el agente de (.+) en su propio chat:"),
        lambda m: f"Try {m[1]}'s agent in its own chat:",
    ),
    (
        re.compile(r"Es el chat real de (.+): lo que escribas acá aparece en la Bandeja\."),
        lambda m: f"It's {m[1]}'s real chat: whatever you type here shows up in the Inbox.",
    ),
    (
        re.compile(r"La prueba de conexión falló: (.+)"),
        lambda m: f"The connection test failed: {m[1]}",
    ),
    # métricas
    (
        re.compile(r"Cómo viene atendiendo el agente de (.+) en los últimos (\d+) días\."),
        lambda m: f"How {m[1]}'s agent has been doing over the last {m[2]} days.",
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


_VOID = {"br", "input", "meta", "link", "img", "hr", "source", "wbr", "col", "area"}
_MESSAGE = re.compile(r'\bclass="(?:[^"]*\s)?msg(?:\s[^"]*)?"|\btranslate="no"')


def translate_html(markup: str, translate_text: Callable[[str], str] = translate_text) -> str:
    """El HTML de una página con su texto traducido (ver el docstring del módulo): al inglés por
    defecto; `translate_text` elige otro catálogo (decisión 085). El texto de un mensaje del chat
    (`class="msg"`) es del cliente o del agente, y lo marcado con `translate="no"` queda tal
    cual."""

    def _attributes(tag: str) -> str:
        def swap(match: re.Match[str]) -> str:
            value = html.unescape(match.group(2))
            done = translate_text(value)
            return match.group(0) if done == value else f'{match.group(1)}="{html.escape(done)}"'

        return _ATTRIBUTE.sub(swap, tag)

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
