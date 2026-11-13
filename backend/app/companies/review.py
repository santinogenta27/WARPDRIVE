"""Revisión del onboarding (decisión 026): la empresa revisa, edita y aprueba los tipos de caso
y las herramientas que el pipeline descubrió.

Lo aprobado pasa al agente en vivo de esa empresa en el acto: el agente lee de la base, en cada
mensaje, los tipos de caso activos y las herramientas aprobadas. Nada queda activo sin que una
persona de la empresa lo apruebe (spec). Es el mismo camino para todas las empresas, también
para Acme (`app/demo/acme.py`).
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
import yaml
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope

UNSET: Any = object()
AMOUNT_SOURCES = ("input.amount", "order.total", "input.percent")
RISKS = ("low", "medium", "high")


class ReviewError(ValueError):
    """Cambio inválido: el mensaje se le muestra a quien revisa."""


@dataclass(frozen=True)
class CaseType:
    id: uuid.UUID
    name: str
    volume: int
    active: bool
    instructions: str
    goal: str
    tools: list[str]


@dataclass(frozen=True)
class Tool:
    id: uuid.UUID
    name: str
    manifest: dict[str, Any]
    approved: bool


def parse_instructions(text: str) -> dict[str, Any]:
    """Instrucciones de un tipo de caso (YAML de la spec). Tienen que tener objetivo."""
    try:
        parsed = yaml.safe_load(text or "")
    except yaml.YAMLError as error:
        raise ReviewError("Las instrucciones no son un YAML válido.") from error
    if not isinstance(parsed, dict) or not str(parsed.get("objetivo") or "").strip():
        raise ReviewError("Las instrucciones tienen que tener un `objetivo`.")
    tools = parsed.get("herramientas") or []
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ReviewError("`herramientas` tiene que ser una lista de nombres.")
    return parsed


def case_types(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[CaseType]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, name, volume, active, instructions from case_types"
            " order by volume desc, name"
        ).fetchall()
    found = []
    for type_id, name, volume, active, instructions in rows:
        parsed = yaml.safe_load(instructions or "") or {}
        if not isinstance(parsed, dict):  # instrucciones que no son un YAML de tipo de caso
            parsed = {}
        found.append(
            CaseType(
                id=type_id,
                name=name,
                volume=volume,
                active=active,
                instructions=instructions or "",
                goal=str(parsed.get("objetivo") or ""),
                tools=[str(t) for t in parsed.get("herramientas") or []],
            )
        )
    return found


def set_case_type(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    type_id: uuid.UUID,
    *,
    active: bool | None = None,
    instructions: str | None = None,
) -> bool:
    """Activa, desactiva o edita un tipo de caso de la empresa. False si no es de la empresa."""
    if instructions is not None:
        parse_instructions(instructions)
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "update case_types set active = coalesce(%s, active),"
            " instructions = coalesce(%s, instructions) where id = %s returning 1",
            (active, instructions, type_id),
        ).fetchone()
    return row is not None


def tools(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[Tool]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, name, manifest, approved from tools order by name"
        ).fetchall()
    return [Tool(*row) for row in rows]


def _amount(value: Any, label: str) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace("$", "").replace(" ", "")
    if "," in text:  # 35.000,50
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):  # 35.000
        text = text.replace(".", "")
    try:
        amount = float(text)
    except ValueError as error:
        raise ReviewError(f"{label} tiene que ser un número.") from error
    if amount < 0:
        raise ReviewError(f"{label} no puede ser negativo.")
    return int(amount) if amount.is_integer() else amount  # 50000, no 50000.0


def set_tool(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    tool_id: uuid.UUID,
    *,
    approved: bool | None = None,
    max_amount: Any = UNSET,
    approval_over: Any = UNSET,
    amount_from: Any = UNSET,
    risk: Any = UNSET,
    alternatives: Any = UNSET,
) -> bool:
    """Aprueba una herramienta o cambia sus límites. `max_amount`: sobre ese monto se rechaza
    siempre. `approval_over`: sobre ese monto pide aprobación humana. Una herramienta de riesgo
    alto pide aprobación siempre: ese límite no se puede quitar. `risk`: la empresa puede
    corregir el riesgo que estimó el generador (un reembolso que solo pide aprobación sobre un
    monto es de riesgo medio, decisión 038). `alternatives`: herramientas que la política permite
    cuando la acción supera el máximo (decisión 039). False si no es de la empresa."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select manifest from tools where id = %s for update", (tool_id,)
        ).fetchone()
        if row is None:
            return False
        manifest: dict[str, Any] = dict(row[0])
        if risk is not UNSET and risk is not None:
            if risk not in RISKS:
                raise ReviewError("Riesgo desconocido.")
            manifest["risk"] = risk
            if risk != "high" and manifest.get("approval_over") == 0:
                manifest["approval_over"] = None
            conn.execute("update tools set risk = %s where id = %s", (risk, tool_id))
        if max_amount is not UNSET:
            manifest["max_amount"] = _amount(max_amount, "El monto máximo")
        if approval_over is not UNSET:
            limit = _amount(approval_over, "El monto que pide aprobación")
            manifest["approval_over"] = 0 if manifest["risk"] == "high" else limit
        if amount_from is not UNSET and amount_from:
            if amount_from not in AMOUNT_SOURCES:
                raise ReviewError("Origen del monto desconocido.")
            manifest["amount_from"] = amount_from
        if alternatives is not UNSET and alternatives is not None:
            names = [str(name) for name in alternatives]
            if names:
                manifest["alternatives"] = names
            else:
                manifest.pop("alternatives", None)
        conn.execute(
            "update tools set manifest = %s, max_amount = %s, approved = coalesce(%s, approved)"
            " where id = %s",
            (Jsonb(manifest), manifest.get("max_amount"), approved, tool_id),
        )
    return True
