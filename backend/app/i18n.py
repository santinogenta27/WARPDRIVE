"""Idioma de cada empresa (decisión 036): `es` (por defecto) o `en`.

El idioma vive en `tenants.settings.language`. Acá están los mensajes fijos que llegan al
cliente, con la misma clave en los dos idiomas. Los nombres internos (herramientas, etiquetas del
prompt, claves del YAML de los tipos de caso) no se traducen: son identificadores.
"""

import uuid
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope

Language = Literal["es", "en"]
LANGUAGES: tuple[Language, ...] = ("es", "en")
DEFAULT: Language = "es"
# El tono del prompt que va con cada idioma (prompt.TONES).
TONE_FOR = {"es": "rioplatense", "en": "english"}
NAMES = {"es": "Español", "en": "English"}


def normalize(value: Any) -> Language:
    return "en" if str(value or "").strip().lower() == "en" else "es"


def tenant_language(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Language:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ->> 'language' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    return normalize(row[0] if row else None)


def set_language(conn: psycopg.Connection, tenant_id: uuid.UUID, language: str) -> Language:
    """Cambia el idioma de la empresa y el tono que le corresponde."""
    lang = normalize(language)
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"language": lang, "tone": TONE_FOR[lang]}), tenant_id),
        )
    return lang


MESSAGES: dict[str, dict[str, str]] = {
    "es": {
        # agente
        "verification": (
            "Para darte datos de tu pedido necesito verificar que sos el titular. "
            "¿Me pasás el email con el que hiciste la compra? Te mando un código para confirmarlo."
        ),
        "approval_pending": (
            "Tu pedido quedó registrado y en revisión: una persona del equipo lo tiene que "
            "aprobar. Te avisamos por este mismo chat apenas esté."
        ),
        "duplicate_approval": (
            "Ya tenemos una solicitud en revisión para ese pedido, así que no hace falta pedirla "
            "de nuevo. Una persona del equipo la está revisando y te avisamos apenas esté."
        ),
        "escalation": (
            "Te paso con una persona del equipo, que te va a responder por este mismo chat."
        ),
        # El caso ya estaba derivado (decisión 097): no se repite la misma frase en cada mensaje.
        "escalation_waiting": (
            "Ya le pasé tu consulta al equipo y te van a responder por este mismo chat. Sumé lo que "
            "me escribiste para que lo vean."
        ),
        "refusal": (
            "Perdón, con eso no puedo ayudarte. Sí puedo darte una mano con tus propios pedidos o "
            "con cualquier consulta sobre la tienda."
        ),
        "code_sent": (
            "Si ese email está registrado en una compra, te llega un código de 6 dígitos. "
            "Escribilo acá para confirmar que sos el titular. Vence en 10 minutos."
        ),
        "verified_prefix": "¡Listo, ya verifiqué tu identidad! ",
        "how_can_i_help": "¿En qué te ayudo?",
        "code_failed": (
            "No pudimos enviarte el código en este momento. Esperá un minuto y pasame de nuevo tu "
            "email, así te mando otro."
        ),
        "code_expired": "Ese código ya venció. Pasame de nuevo tu email y te mando uno nuevo.",
        "code_locked": (
            "Ese código tampoco es correcto y ya no quedan intentos. Si querés, pasame de nuevo tu "
            "email y te mando un código nuevo."
        ),
        "code_wrong_one": "Ese código no es correcto. Te queda 1 intento.",
        "code_wrong_many": "Ese código no es correcto. Te quedan {n} intentos.",
        "chat_fallback": (
            "¡Hola! Soy el asistente virtual de {empresa}. Te puedo ayudar con tus pedidos y con "
            "consultas sobre la tienda. ¿En qué te ayudo?"
        ),
        "store_questions": "consultas de la tienda",
        "email_subject": "Tu código de verificación de {empresa}",
        "email_body": (
            "Tu código para verificar tu identidad en {empresa} es {code}. Vence en {minutes} "
            "minutos."
        ),
        # el mismo email en HTML (decisión 043)
        "email_intro": "Usá este código para confirmar que sos vos en el chat de {empresa}:",
        "email_expires": "Vence en {minutes} minutos.",
        "email_ignore": (
            "Si no lo pediste, ignorá este email: sin el código nadie puede ver tus pedidos."
        ),
        # cierre y CSAT
        "closing": "¡De nada! Si necesitás algo más, escribime cuando quieras.",
        "csat_question": (
            "¿Pudimos resolver tu consulta? Respondé *sí* o *no*. Si querés, contanos por qué."
        ),
        "csat_thanks": "¡Gracias por contarnos! Si necesitás algo más, escribime cuando quieras.",
        "csat_sorry": "Gracias por avisarnos, y disculpá. ",
        # aprobaciones decididas desde el panel
        "approval_rejected": (
            "Revisamos tu solicitud{about} y no la pudimos aprobar. Si querés, respondé este "
            "mensaje y una persona del equipo te ayuda."
        ),
        "approval_failed": (
            "Aprobamos tu solicitud{about}, pero no pudimos completarla automáticamente. "
            "Una persona del equipo te va a escribir para terminarla."
        ),
        "approval_refund": (
            "¡Buenas noticias! Aprobamos el reembolso{about}{amount}. Ya lo iniciamos: te "
            "avisamos cuando se acredite en tu medio de pago."
        ),
        "approval_done": "¡Listo! Aprobamos y realizamos lo que pediste{about}.",
        "about_order": " del pedido {order}",
        "amount_for": " por {amount}",
        # email de confirmación del reembolso aprobado (decisión 046)
        "refund_subject": "Tu reembolso está confirmado – pedido {order}",
        "refund_subject_plain": "Tu reembolso está confirmado",
        "refund_intro": "Buenas noticias: aprobamos el reembolso{about}.",
        "refund_amount": "Monto del reembolso",
        "refund_method": "Se acredita en",
        "refund_when": "Plazo",
        "refund_reference": "Referencia",
        "refund_method_default": "tu medio de pago original",
        "refund_when_default": "unos días hábiles, según tu banco",
        "refund_footer": (
            "Si tenés alguna duda, escribinos por el chat de {empresa} con tu número de referencia."
        ),
        # chat web
        "unsupported": "Por ahora solo puedo leer mensajes de texto. ¿Me lo escribís?",
        "page_title": "{empresa} · Atención al cliente",
        "page_title_plain": "Atención al cliente",
        "page_note": "Chat de {empresa}",
        "greeting": "¡Hola! Soy el asistente virtual de {empresa}. ¿En qué te puedo ayudar?",
        "chat_error": "Tuvimos un problema para responderte. Probá de nuevo en un rato.",
        "forbidden": "Este sitio no está habilitado para usar el chat.",
        "limit_demo_session": (
            "Llegaste al límite de {limit} mensajes de esta conversación. "
            "¡Gracias por probar la demo!"
        ),
        "limit_demo_ip": (
            "Llegaste al límite de uso diario de la demo desde tu conexión. "
            "Volvé a probar mañana. ¡Gracias!"
        ),
        "limit_demo_daily": (
            "La demo llegó a su límite de uso por hoy. Volvé a probar mañana. ¡Gracias!"
        ),
        "limit_session": (
            "Llegaste al límite de {limit} mensajes de esta conversación. Podés empezar una nueva."
        ),
        "limit_ip": (
            "Llegaste al límite de mensajes por hoy desde tu conexión. Volvé a escribirnos mañana."
        ),
        "limit_daily": "El chat llegó a su límite de mensajes por hoy. Volvé a escribirnos mañana.",
        "limit_trial": (
            "La prueba llegó a su límite de mensajes. Para seguir, empezá tu piloto o hablá con "
            "nosotros desde la página de la prueba."
        ),
    },
    "en": {
        "verification": (
            "To share details about your order, I first need to confirm you're the account "
            "holder. Could you send me the email you used for the purchase? I'll email you a "
            "code to confirm it."
        ),
        "approval_pending": (
            "Your request is in and under review: a member of our team needs to approve it. "
            "We'll let you know right here as soon as it's done."
        ),
        "duplicate_approval": (
            "We already have a request under review for that order, so there's no need to "
            "submit it again. A member of our team is looking at it and we'll let you know as "
            "soon as it's done."
        ),
        "escalation": (
            "I'm passing you to a member of our team, who will reply right here in this chat."
        ),
        "escalation_waiting": (
            "I've already passed your request to our team, and they'll reply right here in this "
            "chat. I added your message so they can see it."
        ),
        "refusal": (
            "Sorry, I can't help with that. I'm happy to help with your own orders or any "
            "question about the store."
        ),
        "code_sent": (
            "If that email is linked to a purchase, you'll receive a 6-digit code. Type it here "
            "to confirm you're the account holder. It expires in 10 minutes."
        ),
        "verified_prefix": "All set, you're verified! ",
        "how_can_i_help": "How can I help you?",
        "code_failed": (
            "We couldn't send you the code right now. Please wait a minute and send me your "
            "email again so I can send a new one."
        ),
        "code_expired": "That code has expired. Send me your email again and I'll send a new one.",
        "code_locked": (
            "That code isn't right either, and there are no attempts left. If you like, send me "
            "your email again and I'll send you a new code."
        ),
        "code_wrong_one": "That code isn't right. You have 1 attempt left.",
        "code_wrong_many": "That code isn't right. You have {n} attempts left.",
        "chat_fallback": (
            "Hi! I'm the virtual assistant for {empresa}. I can help you with your orders and "
            "any question about the store. How can I help?"
        ),
        "store_questions": "questions about the store",
        "email_subject": "Your {empresa} verification code",
        "email_body": (
            "Your code to verify your identity with {empresa} is {code}. It expires in {minutes} "
            "minutes."
        ),
        "email_intro": "Use this code to confirm it's you in the {empresa} chat:",
        "email_expires": "It expires in {minutes} minutes.",
        "email_ignore": (
            "If you didn't ask for it, you can ignore this email: no one can see your orders "
            "without the code."
        ),
        "closing": "You're welcome! If you need anything else, just message me anytime.",
        "csat_question": (
            "Did we resolve your question? Reply *yes* or *no*. Feel free to tell us why."
        ),
        "csat_thanks": "Thanks for letting us know! If you need anything else, just message me.",
        "csat_sorry": "Thanks for telling us, and we're sorry. ",
        "approval_rejected": (
            "We reviewed your request{about} and weren't able to approve it. If you like, reply "
            "to this message and a member of our team will help you."
        ),
        "approval_failed": (
            "We approved your request{about}, but couldn't complete it automatically. A member "
            "of our team will message you to finish it."
        ),
        "approval_refund": (
            "Good news! We approved the refund{about}{amount}. It's on its way: we'll let you "
            "know when it reaches your payment method."
        ),
        "approval_done": "All done! We approved and completed your request{about}.",
        "about_order": " for order {order}",
        "amount_for": " of {amount}",
        "refund_subject": "Your refund is confirmed – order {order}",
        "refund_subject_plain": "Your refund is confirmed",
        "refund_intro": "Good news: we approved your refund{about}.",
        "refund_amount": "Refund amount",
        "refund_method": "Refunded to",
        "refund_when": "When you'll see it",
        "refund_reference": "Reference",
        "refund_method_default": "your original payment method",
        "refund_when_default": "a few business days, depending on your bank",
        "refund_footer": (
            "Questions? Message us in the {empresa} chat and mention your reference number."
        ),
        "unsupported": "For now I can only read text messages. Could you type it out for me?",
        "page_title": "{empresa} · Customer support",
        "page_title_plain": "Customer support",
        "page_note": "{empresa} chat",
        "greeting": "Hi! I'm the virtual assistant for {empresa}. How can I help you today?",
        "chat_error": "We had a problem replying. Please try again in a moment.",
        "forbidden": "This site isn't enabled to use the chat.",
        "limit_demo_session": (
            "You've reached the {limit}-message limit for this conversation. Thanks for trying "
            "the demo!"
        ),
        "limit_demo_ip": (
            "You've reached today's demo limit from your connection. Please try again tomorrow. "
            "Thanks!"
        ),
        "limit_demo_daily": (
            "The demo has reached its limit for today. Please try again tomorrow. Thanks!"
        ),
        "limit_session": (
            "You've reached the {limit}-message limit for this conversation. You can start a "
            "new one."
        ),
        "limit_ip": (
            "You've reached today's message limit from your connection. Please write to us "
            "again tomorrow."
        ),
        "limit_daily": "The chat has reached its message limit for today. Please write to us "
        "again tomorrow.",
        "limit_trial": (
            "The trial has reached its message limit. To continue, start your pilot or talk to "
            "us from the trial page."
        ),
    },
}


def text(language: str, key: str, **values: Any) -> str:
    template = MESSAGES[normalize(language)][key]
    return template.format(**values) if values else template


def money(amount: Any, language: str) -> str:
    """Un monto como lo lee el cliente: "$35.000" en español, "$1,250.00" en inglés."""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    if normalize(language) == "en":
        return f"${value:,.2f}".replace(".00", "")
    return "$" + f"{value:,.0f}".replace(",", ".")


def approval_message(language: str, tool: str, params: dict[str, Any], status: str) -> str:
    """El aviso al cliente cuando una persona decide una aprobación (`status`: `rejected`,
    `executed` u otro estado de falla)."""
    order = params.get("order_id")
    about = text(language, "about_order", order=order) if order else ""
    if status == "rejected":
        return text(language, "approval_rejected", about=about)
    if status != "executed":
        return text(language, "approval_failed", about=about)
    if tool == "iniciar_reembolso":
        amount = (
            text(language, "amount_for", amount=money(params["amount"], language))
            if params.get("amount")
            else ""
        )
        return text(language, "approval_refund", about=about, amount=amount)
    return text(language, "approval_done", about=about)
