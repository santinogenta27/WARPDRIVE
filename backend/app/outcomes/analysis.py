"""Análisis de conversaciones (Hito 12, tarea 6; decisión 083).

De los últimos días, sin modelo y sin datos personales:

- temas principales: casos por tipo de caso;
- motivos de derivación: por la causa que registró el agente (decisión 045);
- preguntas sin respuesta: lo que preguntó el cliente en los casos derivados por falta de
  fuente, por un tipo inactivo o porque el agente no encontró qué responder, y las respuestas
  que el equipo marcó como "falta información". Se agrupan por la palabra significativa que más
  comparten.

Los ejemplos pasan por el anonimizador del onboarding (emails, teléfonos, documentos, tarjetas y
nombres conocidos) y se les tapan los números (pedidos, montos).
"""

import re
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import psycopg

from app.db.connection import tenant_scope
from app.onboarding.anonymize import Anonymizer

DAYS = 30
EXAMPLES = 3
MAX_GROUPS = 8
# Causas de derivación en las que el asistente no tuvo con qué responder.
UNANSWERED_CAUSES = ("fundamento", "tipo_inactivo", "agente", "limite_pasos")
_NUMBER = re.compile(r"\b\d[\d.,]{2,}\b")
_STOP = set(
    """a al algo alguna alguno ante como con cual cuando de del donde el ella ellos en entre es
    esa ese esta este esto hay la las le les lo los mas me mi mis muy no nos o para pero por que
    se si sin sobre su sus te tengo tiene tienen tu un una uno y ya yo hola gracias quiero quisiera
    puedo pueden podes hacen hace saber necesito tienda pedido pedidos favor buenas buen dia dias
    the and for you your with have has can could would what when where how is are was it this
    that my me to of in on do does i hi hello thanks please order orders""".split()
)


def _plain(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
    )


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zñ]{4,}", _plain(text)) if t not in _STOP}


def _known_names(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[str]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select name from customer_memories where name <> '' limit 500"
        ).fetchall()
    return [str(r[0]) for r in rows]


def redact(text: str, names: list[str] | None = None) -> str:
    """Un ejemplo sin datos personales: el anonimizador del onboarding y sin números."""
    cleaned = Anonymizer(names or []).anonymize(" ".join(text.split()))
    return _NUMBER.sub("<NUM>", cleaned)[:240]


def group_questions(questions: list[str]) -> list[dict[str, Any]]:
    """Agrupa por la palabra significativa que más preguntas comparten (de a una, en orden)."""
    pending = [(q, _tokens(q)) for q in questions if q.strip()]
    groups: list[dict[str, Any]] = []
    while pending and len(groups) < MAX_GROUPS:
        counts = Counter(token for _, tokens in pending for token in tokens)
        if not counts:
            break
        # Empate: la palabra más larga, que suele ser la más descriptiva ("arreglos", no "ruedo").
        word, hits = max(counts.items(), key=lambda item: (item[1], len(item[0]), item[0]))
        if hits < 2 and groups:
            break  # el resto son preguntas sueltas
        inside = [q for q, tokens in pending if word in tokens]
        groups.append({"tema": word, "cantidad": len(inside), "ejemplos": inside[:EXAMPLES]})
        pending = [(q, tokens) for q, tokens in pending if word not in tokens]
    if pending:
        rest = [q for q, _ in pending]
        groups.append({"tema": "", "cantidad": len(rest), "ejemplos": rest[:EXAMPLES]})
    return groups


def analyze(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    days: int = DAYS,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    end = until or datetime.now().astimezone()
    start = since or end - timedelta(days=days)
    names = _known_names(conn, tenant_id)
    period = {"start": start, "end": end}
    with tenant_scope(conn, tenant_id):
        topics = conn.execute(
            "select t.name, count(*) from cases c left join case_types t on t.id = c.case_type_id"
            " where c.created_at >= %(start)s and c.created_at < %(end)s"
            " and c.archived_at is null group by t.name order by count(*) desc, t.name limit 10",
            period,
        ).fetchall()
        firsts = conn.execute(
            "select t.name, (select m.text from case_messages m where m.case_id = c.id"
            "  and m.role = 'customer' order by m.created_at limit 1)"
            " from cases c left join case_types t on t.id = c.case_type_id"
            " where c.created_at >= %(start)s and c.created_at < %(end)s"
            " and c.archived_at is null order by c.created_at desc limit 300",
            period,
        ).fetchall()
        escalations = conn.execute(
            "select payload -> 'causa' ->> 'tipo', payload ->> 'mensaje_cliente' from audit_events"
            " where kind = 'escalated' and created_at >= %(start)s and created_at < %(end)s"
            " order by created_at desc limit 500",
            period,
        ).fetchall()
        reviewed = conn.execute(
            "select (select m.text from case_messages m where m.case_id = r.case_id"
            "  and m.role = 'customer' and m.created_at < a.created_at"
            "  order by m.created_at desc limit 1)"
            " from reply_reviews r join case_messages a on a.id = r.message_id"
            " where r.reason = 'falta_informacion' and r.updated_at >= %(start)s"
            " and r.updated_at < %(end)s limit 200",
            period,
        ).fetchall()
    examples: dict[str | None, list[str]] = {}
    for kind, text in firsts:
        if text and len(examples.setdefault(kind, [])) < EXAMPLES:
            examples[kind].append(redact(str(text), names))
    reasons = Counter(str(kind or "sin_causa") for kind, _ in escalations)
    reason_examples: dict[str, list[str]] = {}
    for kind, text in escalations:
        key = str(kind or "sin_causa")
        if text and len(reason_examples.setdefault(key, [])) < EXAMPLES:
            reason_examples[key].append(redact(str(text), names))
    unanswered = [
        redact(str(text), names) for kind, text in escalations if kind in UNANSWERED_CAUSES and text
    ] + [redact(str(r[0]), names) for r in reviewed if r[0]]
    return {
        "desde": start.isoformat(timespec="seconds"),
        "hasta": end.isoformat(timespec="seconds"),
        "temas": [
            {"tipo": name, "casos": int(n), "ejemplos": examples.get(name, [])}
            for name, n in topics
        ],
        "derivaciones": [
            {"causa": kind, "casos": n, "ejemplos": reason_examples.get(kind, [])}
            for kind, n in reasons.most_common(10)
        ],
        "sin_respuesta": {"total": len(unanswered), "grupos": group_questions(unanswered)},
    }
