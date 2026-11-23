"""Auditoría encadenada: cada evento de un caso guarda el hash del anterior.

El hash cubre tenant, caso, secuencia, tipo, payload, fecha y hash anterior: modificar, borrar
o reordenar un evento rompe la cadena. La tabla es solo inserción (permisos + trigger); la cadena
detecta además cambios hechos por alguien con acceso de dueño a la base.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: str
    prev_hash: str | None
    hash: str


@dataclass(frozen=True)
class ChainVerdict:
    ok: bool
    broken_at: int | None = None
    reason: str = ""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _event_hash(
    tenant_id: uuid.UUID,
    case_id: uuid.UUID | None,
    seq: int,
    kind: str,
    payload: dict[str, Any],
    created_at: str,
    prev_hash: str | None,
) -> str:
    body = _canonical(
        {
            "tenant_id": str(tenant_id),
            "case_id": str(case_id),
            "seq": seq,
            "kind": kind,
            "payload": payload,
            "created_at": created_at,
            "prev_hash": prev_hash,
        }
    )
    return hashlib.sha256(body.encode()).hexdigest()


def record(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID | None,
    kind: str,
    payload: dict[str, Any],
) -> AuditEvent:
    """Agrega un evento al final de la cadena del caso. Sin caso (`None`): un cambio de la
    empresa hecho desde el panel, como corregir o borrar la memoria de un cliente."""
    normalized: dict[str, Any] = json.loads(_canonical(payload))  # tal como lo devuelve jsonb
    created_at = datetime.now(UTC).isoformat()
    with tenant_scope(conn, tenant_id):
        # Bloquear el caso serializa los agregados: dos eventos no pueden tomar la misma secuencia.
        # El bloqueo y el último evento salen en la misma consulta (un viaje menos por evento).
        last = conn.execute(
            "select e.seq, e.hash from cases c left join lateral (select seq, hash"
            " from audit_events where case_id = c.id order by seq desc limit 1) e on true"
            " where c.id = %s for update of c",
            (case_id,),
        ).fetchone()
        seq = (last[0] + 1) if last and last[0] is not None else 1
        prev_hash = last[1] if last else None
        digest = _event_hash(tenant_id, case_id, seq, kind, normalized, created_at, prev_hash)
        conn.execute(
            "insert into audit_events (tenant_id, case_id, seq, kind, payload, created_at,"
            " prev_hash, hash) values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (tenant_id, case_id, seq, kind, Jsonb(normalized), created_at, prev_hash, digest),
        )
    return AuditEvent(seq, kind, normalized, created_at, prev_hash, digest)


def reconstruct_case(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[AuditEvent]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select seq, kind, payload, created_at, prev_hash, hash from audit_events"
            " where case_id = %s order by seq",
            (case_id,),
        ).fetchall()
    return [
        AuditEvent(r[0], r[1], r[2], r[3].astimezone(UTC).isoformat(), r[4], r[5]) for r in rows
    ]


def verify_chain(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> ChainVerdict:
    previous: str | None = None
    for expected_seq, event in enumerate(reconstruct_case(conn, tenant_id, case_id), start=1):
        if event.seq != expected_seq:
            return ChainVerdict(False, event.seq, f"falta el evento {expected_seq}")
        if event.prev_hash != previous:
            return ChainVerdict(False, event.seq, "el hash anterior no coincide")
        recomputed = _event_hash(
            tenant_id,
            case_id,
            event.seq,
            event.kind,
            event.payload,
            event.created_at,
            event.prev_hash,
        )
        if recomputed != event.hash:
            return ChainVerdict(False, event.seq, "el contenido del evento fue modificado")
        previous = event.hash
    return ChainVerdict(True)
