"""CSAT (decisión 033): al cerrar un caso, el chat pregunta si la consulta se resolvió.

La pregunta va en el mismo mensaje de cierre, así funciona igual en el chat web, en el widget y
en WhatsApp. El cliente responde "sí" o "no", con un comentario si quiere. Si escribe otra cosa,
es una consulta nueva y la pregunta queda sin responder.
"""

import re
import uuid
from typing import Any

import psycopg

from app.db.connection import tenant_scope

QUESTION = "¿Pudimos resolver tu consulta? Respondé *sí* o *no*. Si querés, contanos por qué."
THANKS = "¡Gracias por contarnos! Si necesitás algo más, escribime cuando quieras."
SORRY = "Gracias por avisarnos, y disculpá. "  # sigue el aviso de que lo atiende una persona
NOT_RESOLVED = "El cliente respondió que su consulta no se resolvió."
ANSWER_HOURS = 24  # después, un "sí" suelto ya no se toma como respuesta

# "sí" o "no" al principio. Después, nada, o un comentario separado por un signo. "si compro
# hoy…" (condicional, sin tilde y sin signo) es otra consulta.
_ANSWER = re.compile(r"^\s*(s[ií]+|no+|\U0001F44D|\U0001F44E)(?![\w])(.*)$", re.I | re.S)
_SEPARATOR = re.compile(r"^\s*[,.;:!…-]+\s*")


def parse(text: str) -> tuple[bool, str] | None:
    """(¿se resolvió?, comentario) si el mensaje responde la pregunta; si no, None."""
    match = _ANSWER.match(text)
    if match is None:
        return None
    word, rest = match.group(1).lower(), match.group(2)
    yes = word.startswith("s") or word == "\U0001f44d"
    if not rest.strip(" \t\n!.¡"):
        return yes, ""
    if _SEPARATOR.match(rest):
        return yes, _SEPARATOR.sub("", rest, count=1).strip()
    if yes and "í" not in word:
        return None  # "si compro hoy, ¿cuándo llega?"
    # "No me sirvió la respuesta": el comentario es el mensaje entero.
    return yes, text.strip()


def ask(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID) -> bool:
    """Anota que se le preguntó al cliente. False si ese caso ya se había preguntado: a cada
    caso se le pregunta una sola vez."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into csat_responses (tenant_id, case_id) values (%s, %s)"
            " on conflict (tenant_id, case_id) do nothing returning id",
            (tenant_id, case_id),
        ).fetchone()
    return row is not None


def asked(conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID) -> bool:
    with tenant_scope(conn, tenant_id):
        row = conn.execute("select 1 from csat_responses where case_id = %s", (case_id,)).fetchone()
    return row is not None


def pending(
    conn: psycopg.Connection, tenant_id: uuid.UUID, contact_id: uuid.UUID
) -> uuid.UUID | None:
    """El caso del contacto que espera la respuesta: resuelto, preguntado hace menos de 24 h y
    sin responder."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select r.case_id from csat_responses r join cases c on c.id = r.case_id"
            " where c.contact_id = %s and c.status = 'resolved' and c.archived_at is null"
            " and r.answered_at is null and r.asked_at > now() - make_interval(hours => %s)"
            " order by r.asked_at desc limit 1",
            (contact_id, ANSWER_HOURS),
        ).fetchone()
    return row[0] if row else None


def answer(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID, resolved: bool, comment: str
) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update csat_responses set resolved = %s, comment = %s, answered_at = now()"
            " where case_id = %s and answered_at is null",
            (resolved, comment[:1000] or None, case_id),
        )


def summary(conn: psycopg.Connection, tenant_id: uuid.UUID, *, days: int = 30) -> dict[str, Any]:
    """El CSAT de los últimos `days` días, para la página Métricas."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select count(*), count(answered_at), count(*) filter (where resolved),"
            " count(*) filter (where resolved = false) from csat_responses"
            " where asked_at > now() - make_interval(days => %s)",
            (days,),
        ).fetchone()
        comments = conn.execute(
            "select answered_at, resolved, comment, case_id from csat_responses"
            " where comment is not null and asked_at > now() - make_interval(days => %s)"
            " order by answered_at desc, id desc limit 10",
            (days,),
        ).fetchall()
    assert row is not None
    return {
        "preguntas": row[0],
        "respuestas": row[1],
        "si": row[2],
        "no": row[3],
        "satisfaccion": row[2] / row[1] if row[1] else None,
        "comentarios": [
            {"fecha": at, "resuelto": resolved, "comentario": comment, "caso": case_id}
            for at, resolved, comment, case_id in comments
        ],
    }
