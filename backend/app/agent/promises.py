"""Promesas cumplidas (Hito 12, tarea 7; decisión 084).

Una promesa a futuro ("te avisamos cuando…", "we'll let you know") solo se puede hacer si existe
el aviso programado que la cumple. Hoy el sistema cumple dos avisos solo:

- `approval_decision`: al decidir una aprobación, el cliente recibe el resultado por el chat;
- `team_reply`: un caso derivado lo responde una persona del equipo desde el panel.

Se programan al crear la aprobación o al derivar, y se cumplen cuando llega el aviso.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from app.audit import log as audit_log
from app.db.connection import tenant_scope

APPROVAL, TEAM = "approval_decision", "team_reply"
REASON = "promete un aviso a futuro sin un aviso programado que lo cumpla"
CORRECTIONS = {
    "es": (
        "Respuesta rechazada: promete avisarle al cliente más adelante, pero no hay ningún aviso "
        "programado que lo cumpla. No prometas avisar, escribir ni contactar: decile cómo puede "
        "consultarlo él (por ejemplo, volviendo a escribir por acá) y respondé lo que sí sabés."
    ),
    "en": (
        "Answer rejected: it promises to notify the customer later, but there's no scheduled "
        "notice to keep that promise. Don't promise to let them know, email or contact them: tell "
        "them how they can check it themselves (for example, by writing here again) and answer "
        "what you do know."
    ),
}
# En futuro siempre es una promesa: "te vamos a escribir", "le notificaremos", "nos vamos a
# comunicar". En presente solo con algo que la lleve al futuro: "te avisamos cuando salga" sí,
# "te avisamos que tu pedido salió" no (informa ahora).
_MESSAGE = (
    r"(?:(?:un\s+)?(?:mensaje|e-?mail|mail|correo|aviso)|por\s+(?:e-?mail|mail|correo|whatsapp))"
)
_NOTICE_VERB = (
    r"(?:avisamos|escribimos|contactamos|notificamos"
    rf"|mandamos\s+{_MESSAGE}|enviamos\s+{_MESSAGE})"
)
_PROMISE_ES = re.compile(
    r"(?i)\b(?:te|le|les)\s+(?:(?:vamos|voy|van)\s+a\s+(?:avisar|escribir|contactar|notificar"
    rf"|mandar\s+{_MESSAGE}|enviar\s+{_MESSAGE})"
    r"|avisaremos|escribiremos|contactaremos|notificaremos)\b"
    r"|\b(?:te|le|les)\s+(?:avisamos|escribimos|contactamos|notificamos"
    rf"|mandamos\s+{_MESSAGE}|enviamos\s+{_MESSAGE})\b(?:\s+\S+){{0,6}}?\s+"
    r"(?:cuando|apenas|en\s+cuanto|ni\s+bien|m[aá]s\s+adelante|pronto|en\s+breve|ma[ñn]ana"
    r"|por\s+ac[aá]|por\s+este)\b"
    r"|\bnos\s+(?:vamos\s+a\s+(?:comunicar|poner\s+en\s+contacto)|comunicaremos"
    r"|pondremos\s+en\s+contacto)\b"
    # Lo que lo lleva al futuro, antes: "cuando eso pase, te enviamos un email" (visto con Gemini).
    r"|\b(?:cuando|apenas|en\s+cuanto|ni\s+bien)\b[^.!?\n]{0,60}?\b(?:te|le|les)\s+"
    rf"{_NOTICE_VERB}\b"
)
# Un aviso que manda el sistema de la tienda, descripto en sus documentos: "Cuando el pedido se
# despacha, te enviamos por email el número de seguimiento", "we email you a prepaid label".
_DOCUMENTED = re.compile(
    rf"(?i)\b(?:te|le|les)\s+{_NOTICE_VERB}\b"
    r"|\bwe\s+(?:email|text|message)\s+you\b|\byou\s+(?:get|receive)\s+an?\s+(?:email|message)\b"
    r"|\bwe\s+send\s+you\s+an?\s+(?:email|message)\b"
)
_PROMISE_EN = re.compile(
    r"(?i)\b(?:we|i)(?:'ll|\s+will|\s+are\s+going\s+to|'re\s+going\s+to)\s+"
    r"(?:let\s+you\s+know|notify\s+you|update\s+you|email\s+you|text\s+you|message\s+you"
    r"|get\s+back\s+to\s+you|reach\s+out|keep\s+you\s+(?:posted|updated|informed)|follow\s+up)\b"
    r"|\byou(?:'ll|\s+will)\s+(?:get|receive)\s+(?:an?\s+)?(?:email|message|notification|update"
    r"|text)\b"
)


def promised(text: str) -> bool:
    """La respuesta promete avisarle al cliente más adelante (en español o en inglés)."""
    return bool(_PROMISE_ES.search(text) or _PROMISE_EN.search(text))


def documented(text: str) -> bool:
    """El documento describe un aviso que manda el sistema de la tienda: una promesa que lo cita
    está respaldada (lo cumple la tienda, no hace falta un aviso programado acá)."""
    return bool(promised(text) or _DOCUMENTED.search(text))


@dataclass(frozen=True)
class Notice:
    id: uuid.UUID
    kind: str
    reference: str
    status: str
    created_at: datetime
    done_at: datetime | None


def schedule(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    kind: str,
    reference: str = "",
) -> bool:
    """Programa un aviso (idempotente). Devuelve si es nuevo."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into scheduled_notices (tenant_id, case_id, kind, reference)"
            " values (%s, %s, %s, %s) on conflict (tenant_id, case_id, kind, reference)"
            " do nothing returning id",
            (tenant_id, case_id, kind, reference),
        ).fetchone()
    if row is not None:
        audit_log.record(
            conn, tenant_id, case_id, "notice_scheduled", {"aviso": kind, "referencia": reference}
        )
    return row is not None


def fulfill(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    kind: str,
    reference: str | None = None,
) -> int:
    """El aviso llegó al cliente: lo marca cumplido. Sin `reference`, todos los de ese tipo."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "update scheduled_notices set status = 'sent', done_at = now()"
            " where case_id = %s and kind = %s and status = 'pending'"
            " and (%s::text is null or reference = %s) returning kind, reference",
            (case_id, kind, reference, reference),
        ).fetchall()
    for found_kind, found_reference in rows:
        audit_log.record(
            conn,
            tenant_id,
            case_id,
            "notice_sent",
            {"aviso": found_kind, "referencia": found_reference},
        )
    return len(rows)


def pending(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[Notice]:
    """Los avisos pendientes del caso. Un aviso de una aprobación que ya no espera (se canceló
    o se decidió sin avisar) queda cancelado."""
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update scheduled_notices n set status = 'cancelled', done_at = now()"
            " from approvals a where n.case_id = %s and n.kind = %s and n.status = 'pending'"
            " and a.id::text = n.reference and a.status <> 'pending'",
            (case_id, APPROVAL),
        )
        rows = conn.execute(
            "select id, kind, reference, status, created_at, done_at from scheduled_notices"
            " where case_id = %s and status = 'pending' order by created_at",
            (case_id,),
        ).fetchall()
    return [Notice(*r) for r in rows]


def for_case(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID) -> list[Notice]:
    """Todos los avisos del caso, para el panel."""
    pending(conn, tenant_id, case_id)  # actualiza los cancelados
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, kind, reference, status, created_at, done_at from scheduled_notices"
            " where case_id = %s order by created_at",
            (case_id,),
        ).fetchall()
    return [Notice(*r) for r in rows]
