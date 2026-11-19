"""Revisión de conversaciones (Hito 11, decisión 073).

El equipo marca cada respuesta del asistente como correcta o incorrecta, con un motivo y un
comentario (`mark`). Las incorrectas forman la lista "Para mejorar" (`to_improve`). Con la
respuesta esperada que escribe el equipo, cada una se convierte en un caso de eval de la empresa
(`to_eval`), con el mismo esquema que los del onboarding: el reporte de preparación lo corre junto
con los demás.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg
from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from app.audit import log as audit_log
from app.db.connection import tenant_scope
from app.onboarding.evalgen import ACTIONS, EVAL_CASE_SCHEMA

VERDICTS = {"correcta": "correct", "incorrecta": "incorrect"}
REASONS = {
    "falta_informacion": "Información faltante",
    "informacion_incorrecta": "Información incorrecta",
    "accion_equivocada": "Acción equivocada",
    "debio_derivar": "Debía derivar a una persona",
    "tono": "Tono",
    "otro": "Otro",
}
MAX_COMMENT = 500
MAX_EXPECTED = 2000


class ReviewError(Exception):
    """La revisión no corresponde (otro caso, ya convertida o sin respuesta esperada)."""


@dataclass(frozen=True)
class Mark:
    verdict: str  # correct | incorrect
    reason: str | None
    comment: str


@dataclass(frozen=True)
class ToImprove:
    id: uuid.UUID
    case_id: uuid.UUID
    question: str
    reply: str
    reason: str | None
    comment: str
    case_type: str | None
    no_sources: bool  # la búsqueda en los documentos del caso no encontró nada
    updated_at: datetime


def mark(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    verdict: str,
    reason: str,
    comment: str,
    reviewed_by: uuid.UUID,
) -> None:
    """Marca una respuesta del asistente de ese caso. Volver a marcarla reemplaza la anterior."""
    if verdict not in VERDICTS:
        raise ReviewError("Elegí si la respuesta fue correcta o incorrecta.")
    stored = VERDICTS[verdict]
    cause = reason if stored == "incorrect" and reason in REASONS else None
    comment = " ".join(comment.split())[:MAX_COMMENT]
    with tenant_scope(conn, tenant_id):
        found = conn.execute(
            "select 1 from case_messages where id = %s and case_id = %s and role = 'agent'",
            (message_id, case_id),
        ).fetchone()
        if found is None:
            raise ReviewError("Esa respuesta no es del asistente en este caso.")
        conn.execute(
            "insert into reply_reviews (tenant_id, case_id, message_id, verdict, reason, comment,"
            " reviewed_by) values (%s, %s, %s, %s, %s, %s, %s)"
            " on conflict (tenant_id, message_id) do update set verdict = excluded.verdict,"
            " reason = excluded.reason, comment = excluded.comment,"
            " reviewed_by = excluded.reviewed_by, updated_at = now()",
            (tenant_id, case_id, message_id, stored, cause, comment, reviewed_by),
        )
    audit_log.record(
        conn,
        tenant_id,
        case_id,
        "reply_reviewed",
        {
            "message_id": str(message_id),
            "verdict": stored,
            "reason": cause,
            "comment": comment,
            "by": str(reviewed_by),
        },
    )


def marks(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> dict[uuid.UUID, Mark]:
    """Las revisiones del caso, por mensaje."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select message_id, verdict, reason, comment from reply_reviews where case_id = %s",
            (case_id,),
        ).fetchall()
    return {row[0]: Mark(row[1], row[2], row[3]) for row in rows}


def to_improve(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[ToImprove]:
    """Las incorrectas que todavía no son un caso de eval, de la más reciente a la más vieja."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select r.id, r.case_id,"
            " coalesce((select q.text from case_messages q where q.case_id = r.case_id"
            "  and q.role = 'customer' and q.created_at < m.created_at"
            "  order by q.created_at desc limit 1), ''),"
            " m.text, r.reason, r.comment, t.name,"
            " exists (select 1 from audit_events e where e.case_id = r.case_id"
            "  and e.kind = 'rag_search' and coalesce(jsonb_array_length(e.payload -> 'sources'),"
            "  0) = 0),"
            " r.updated_at"
            " from reply_reviews r join case_messages m on m.id = r.message_id"
            " join cases c on c.id = r.case_id left join case_types t on t.id = c.case_type_id"
            " where r.verdict = 'incorrect' and r.eval_case_id is null"
            " order by r.updated_at desc limit 100"
        ).fetchall()
    return [ToImprove(*row) for row in rows]


def to_eval(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    *,
    expected: str,
    keywords: str,
    created_by: uuid.UUID,
) -> uuid.UUID:
    """Convierte una incorrecta en un caso de eval de la empresa. Devuelve el id del caso."""
    expected = expected.strip()[:MAX_EXPECTED]
    if not expected:
        raise ReviewError("Escribí la respuesta esperada.")
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select r.case_id, r.reason, c.case_type_id,"
            " (select q.text from case_messages q where q.case_id = r.case_id"
            "  and q.role = 'customer' and q.created_at < m.created_at"
            "  order by q.created_at desc limit 1)"
            " from reply_reviews r join case_messages m on m.id = r.message_id"
            " join cases c on c.id = r.case_id"
            " where r.id = %s and r.verdict = 'incorrect' and r.eval_case_id is null"
            " for update of r",
            (review_id,),
        ).fetchone()
    if row is None or not row[3]:
        raise ReviewError("Esa revisión no existe o ya es un caso de eval.")
    case_id, reason, type_id, question = row
    escalate = reason == "debio_derivar"
    expected_case = {
        "accion": "derivar" if escalate else "responder",
        "acciones_validas": ["derivar"] if escalate else [a for a in ACTIONS if a != "derivar"],
        "debe_escalar": escalate,
        "palabras_clave": [k.strip() for k in keywords.split(",") if k.strip()][:10],
        "categoria": "revision",
        "respuesta_esperada": expected,
    }
    case = {
        "mensaje": question,
        "contacto": "verificado",
        "esperado": expected_case,
        "origen": f"revision:{case_id}",
    }
    Draft202012Validator(EVAL_CASE_SCHEMA).validate(case)
    with tenant_scope(conn, tenant_id):
        created = conn.execute(
            "insert into eval_cases (tenant_id, case_type_id, input, expected, must_escalate,"
            " origin) values (%s, %s, %s, %s, %s, %s) returning id",
            (
                tenant_id,
                type_id,
                Jsonb({"mensaje": question, "contacto": "verificado"}),
                Jsonb(expected_case),
                escalate,
                case["origen"],
            ),
        ).fetchone()
        assert created is not None
        conn.execute(
            "update reply_reviews set eval_case_id = %s, updated_at = now() where id = %s",
            (created[0], review_id),
        )
    eval_case_id: uuid.UUID = created[0]
    audit_log.record(
        conn,
        tenant_id,
        case_id,
        "eval_case_created",
        {"review_id": str(review_id), "eval_case_id": str(eval_case_id), "by": str(created_by)},
    )
    return eval_case_id
