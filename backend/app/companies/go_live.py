"""Salir en vivo (decisión 056): el widget atiende en el sitio de la empresa recién cuando el
reporte de preparación pasa un umbral y la empresa toca "Salir en vivo".

* **Listo** si el último reporte de preparación (evals generados con su propio historial)
  estima una resolución de al menos `GO_LIVE_MIN_RESOLUTION` (0,6 por defecto) y frena todos los
  casos adversariales.
* **Antes de salir**, el chat funciona en el panel (vista previa y playground) y en su página
  propia `/c/…` para probarlo; en los dominios de la empresa, no.
* Las empresas de demo (Acme y Northwind) están siempre en vivo: la demo no cambia.
"""

import os
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope


class GoLiveError(ValueError):
    pass


@dataclass(frozen=True)
class Readiness:
    ready: bool
    live: bool
    resolution: float | None
    threshold: float
    reasons: tuple[str, ...]
    attacks_ok: bool = True
    # pass^3 de la última simulación (decisión 080): informativo, no cambia el umbral.
    simulation: float | None = None


def threshold() -> float:
    try:
        return float(os.environ.get("GO_LIVE_MIN_RESOLUTION") or 0.6)
    except ValueError:
        return 0.6


def _report(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select report from onboarding_runs where status = 'done' and report is not null"
            " order by updated_at desc limit 1"
        ).fetchone()
    return row[0] if row else None


def is_live(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    from app.companies.connection import mode_for

    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select (settings ->> 'live')::boolean from tenants where id = %s", (tenant_id,)
        ).fetchone()
    return bool(row and row[0]) or mode_for(conn, tenant_id) == "demo"


def is_paused(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    """Salió en vivo y la empresa lo pausó (decisión 090): el chat sigue en su sitio, pero cada
    mensaje pasa a una persona del equipo sin que responda el agente. Antes de salir en vivo
    nunca está en pausa (el playground y la página de prueba atienden)."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ? 'live' and not (settings ->> 'live')::boolean from tenants"
            " where id = %s",
            (tenant_id,),
        ).fetchone()
    return bool(row and row[0]) and not is_live(conn, tenant_id)


def _simulation(conn: psycopg.Connection, tenant_id: uuid.UUID) -> float | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select summary ->> 'pass3' from simulation_runs where status = 'done'"
            " order by updated_at desc limit 1"
        ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def readiness(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Readiness:
    limit = threshold()
    report = _report(conn, tenant_id)
    live = is_live(conn, tenant_id)
    simulated = _simulation(conn, tenant_id)
    if report is None:
        reason = "Falta el reporte de preparación: subí tu historial y corré el análisis."
        return Readiness(False, live, None, limit, (reason,), simulation=simulated)
    estimate = float(report.get("resolucion_estimada") or 0)
    adversarial = report.get("adversariales") or {}
    reasons = []
    attacks_ok = int(adversarial.get("superados") or 0) >= int(adversarial.get("total") or 0)
    if estimate < limit:
        reasons.append(
            f"Resolvería solo el {estimate:.0%} y el mínimo para salir es {limit:.0%}: revisá "
            "lo que falta en el reporte."
        )
    if not attacks_ok:
        reasons.append("Hay ataques o pedidos indebidos que no frenó: revisalos en el reporte.")
    return Readiness(
        not reasons, live, estimate, limit, tuple(reasons), attacks_ok, simulation=simulated
    )


def set_live(conn: psycopg.Connection, tenant_id: uuid.UUID, on: bool) -> None:
    if on:
        found = readiness(conn, tenant_id)
        if not found.ready:
            raise GoLiveError(" ".join(found.reasons))
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"live": on}), tenant_id),
        )
