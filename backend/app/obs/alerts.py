"""Alertas en los logs (decisión 028): una línea `ALERTA <tipo> clave=valor…` de nivel WARNING
cuando la latencia, el costo por caso o los errores pasan un umbral. Cualquier recolector de
logs (el de Render incluido) puede avisar filtrando por `ALERTA`.

Umbrales, con los valores de la spec por defecto:

- `ALERT_LATENCY_MS` (10000): un turno sin herramientas tarda más ("primera respuesta < 10 s").
- `ALERT_LATENCY_TOOLS_MS` (20000): un turno con herramientas tarda más ("con herramienta < 20 s").
- `ALERT_COST_PER_CASE_USD` (0.50): el costo de un caso cruza el umbral. Avisa una vez, al cruzar.
- `ALERT_ERRORS` (5) en `ALERT_ERRORS_WINDOW_S` (600): errores de una empresa en la ventana.
"""

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

logger = logging.getLogger("app.alerts")
_errors: dict[uuid.UUID, list[float]] = {}
_lock = threading.Lock()


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _alert(kind: str, **fields: Any) -> str:
    line = f"ALERTA {kind} " + " ".join(f"{key}={value}" for key, value in fields.items())
    logger.warning(line)
    return line


def check_turn(
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    summary: dict[str, Any],
    *,
    case_cost: Decimal,
    turn_cost: Decimal,
) -> list[str]:
    """Revisa un turno terminado. `summary`: el de `Timer.summary()`. `case_cost`: el costo
    total del caso con este turno incluido. Devuelve las alertas que registró."""
    alerts: list[str] = []
    with_tools = int(summary["llamadas"]["herramientas"]) > 0
    limit = (
        _number("ALERT_LATENCY_TOOLS_MS", 20000)
        if with_tools
        else _number("ALERT_LATENCY_MS", 10000)
    )
    total = int(summary["total_ms"])
    if total > limit:
        slowest = max(
            ("modelo", "base", "busqueda", "herramientas"), key=lambda k: int(summary[f"{k}_ms"])
        )
        alerts.append(
            _alert(
                "latencia",
                tenant=tenant_id,
                caso=case_id,
                total_ms=total,
                umbral_ms=int(limit),
                paso_mas_lento=slowest,
            )
        )
    cost_limit = Decimal(str(_number("ALERT_COST_PER_CASE_USD", 0.50)))
    if case_cost >= cost_limit > case_cost - turn_cost:
        alerts.append(
            _alert(
                "costo_por_caso",
                tenant=tenant_id,
                caso=case_id,
                costo_usd=f"{case_cost:.4f}",
                umbral_usd=cost_limit,
            )
        )
    return alerts


def record_error(
    tenant_id: uuid.UUID,
    kind: str,
    *,
    persist: Callable[[uuid.UUID, str, str], None] | None = None,
    detail: str = "",
    now: float | None = None,
) -> list[str]:
    """Registra un error que cortó un turno. `persist` lo guarda para la página Métricas (en su
    propia transacción: la del turno se revirtió). Avisa cuando los errores de la empresa en la
    ventana llegan al umbral, y de nuevo en cada múltiplo."""
    moment = now if now is not None else time.time()
    window = _number("ALERT_ERRORS_WINDOW_S", 600)
    limit = int(_number("ALERT_ERRORS", 5))
    with _lock:
        recent = [t for t in _errors.get(tenant_id, []) if t > moment - window] + [moment]
        _errors[tenant_id] = recent
        count = len(recent)
    if persist is not None:
        try:
            persist(tenant_id, kind, detail[:500])
        except Exception:  # noqa: BLE001 — no poder guardar el error no puede sumar otro
            logger.exception("No se pudo guardar el error")
    alerts: list[str] = []
    if count % limit == 0:  # al llegar al umbral y en cada múltiplo
        alerts.append(
            _alert(
                "errores",
                tenant=tenant_id,
                errores=count,
                ventana_s=int(window),
                umbral=limit,
                ultimo=kind,
            )
        )
    return alerts


def reset() -> None:
    with _lock:
        _errors.clear()


def persist_error(tenant_id: uuid.UUID, kind: str, detail: str) -> None:
    """Guarda el error en `error_events` con una conexión propia (producción)."""
    from app.db.connection import connect, tenant_scope

    with connect() as conn, tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into error_events (tenant_id, kind, detail) values (%s, %s, %s)",
            (tenant_id, kind, detail),
        )
