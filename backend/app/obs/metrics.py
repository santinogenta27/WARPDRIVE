"""Los números de la página Métricas de una empresa (decisión 028). Todo sale de lo que el
sistema ya registra: casos, auditoría (`turn_timing`, derivaciones, herramientas), `usage`
(tokens, costo y eventos del proveedor), `error_events` y `eval_runs`.
"""

import uuid
from typing import Any

import psycopg

from app.cases import csat
from app.cases.billing import billing_summary
from app.db.connection import tenant_scope


def collect(conn: psycopg.Connection, tenant_id: uuid.UUID, *, days: int = 30) -> dict[str, Any]:
    """Métricas de los últimos `days` días de la empresa."""
    with tenant_scope(conn, tenant_id):
        cases = conn.execute(
            "select count(*),"
            " count(*) filter (where status = 'resolved' and resolved_by = 'agent'),"
            " count(*) filter (where status = 'resolved' and resolved_by = 'human'),"
            " count(*) filter (where exists (select 1 from audit_events e"
            "  where e.case_id = c.id and e.kind = 'escalated')),"
            " count(*) filter (where status in ('open', 'waiting_approval', 'escalated')),"
            " count(*) filter (where reopened)"
            " from cases c where archived_at is null"
            " and created_at > now() - make_interval(days => %s)",
            (days,),
        ).fetchone()
        latency = conn.execute(
            "select count(*),"
            " percentile_cont(0.5) within group (order by (payload ->> 'total_ms')::numeric),"
            " percentile_cont(0.95) within group (order by (payload ->> 'total_ms')::numeric),"
            " avg((payload ->> 'modelo_ms')::numeric), avg((payload ->> 'base_ms')::numeric),"
            " avg((payload ->> 'busqueda_ms')::numeric),"
            " avg((payload ->> 'herramientas_ms')::numeric)"
            " from audit_events where kind = 'turn_timing'"
            " and created_at > now() - make_interval(days => %s)",
            (days,),
        ).fetchone()
        model_calls = conn.execute(
            "select count(*), count(*) filter (where 'respaldo' = any(events)),"
            " count(*) filter (where 'recuperada' = any(events)),"
            " count(*) filter (where 'rechazo' = any(events))"
            " from usage where kind = 'llm' and created_at > now() - make_interval(days => %s)",
            (days,),
        ).fetchone()
        tool_failures = conn.execute(
            "select count(*) from audit_events where kind = 'tool_call'"
            " and payload ->> 'status' = 'failed'"
            " and created_at > now() - make_interval(days => %s)",
            (days,),
        ).fetchone()
        errors = conn.execute(
            "select kind, count(*), max(created_at) from error_events"
            " where created_at > now() - make_interval(days => %s)"
            " group by kind order by count(*) desc",
            (days,),
        ).fetchall()
        by_day = conn.execute(
            "select (created_at at time zone 'America/Argentina/Buenos_Aires')::date as day,"
            " count(*) filter (where kind = 'llm'), sum(input_tokens + cache_read_input_tokens"
            " + cache_creation_input_tokens), sum(output_tokens), sum(cost_usd)"
            " from usage where created_at > now() - interval '14 days'"
            " group by day order by day desc"
        ).fetchall()
        by_model = conn.execute(
            "select model, kind, count(*), sum(input_tokens + cache_read_input_tokens"
            " + cache_creation_input_tokens), sum(output_tokens), sum(cost_usd)"
            " from usage where created_at > now() - make_interval(days => %s)"
            " group by model, kind order by sum(cost_usd) desc, count(*) desc",
            (days,),
        ).fetchall()
        by_case = conn.execute(
            "select u.case_id, count(*), sum(u.input_tokens + u.output_tokens), sum(u.cost_usd),"
            " max(u.created_at) from usage u join cases c on c.id = u.case_id"
            " where c.archived_at is null and u.created_at > now() - make_interval(days => %s)"
            " group by u.case_id order by sum(u.cost_usd) desc, sum(u.input_tokens"
            " + u.output_tokens) desc limit 10",
            (days,),
        ).fetchall()
        case_cost = conn.execute(
            "select avg(total), max(total) from (select sum(cost_usd) as total from usage"
            " where case_id is not null and created_at > now() - make_interval(days => %s)"
            " group by case_id) per_case",
            (days,),
        ).fetchone()
        evals = conn.execute(
            "select created_at, mode, prompt_version, model, agent_version, scores"
            " from eval_runs order by created_at desc limit 20"
        ).fetchall()
    assert cases and latency and model_calls and tool_failures and case_cost
    total, by_agent, by_human, escalated, in_progress, reopened = cases
    resolved = by_agent + by_human
    return {
        "dias": days,
        "casos": {
            "total": total,
            "resueltos_asistente": by_agent,
            "resueltos_persona": by_human,
            "derivados": escalated,
            "en_curso": in_progress,
            "reabiertos": reopened,
            # De los casos ya resueltos, cuántos resolvió el asistente sin una persona.
            "tasa_resolucion": by_agent / resolved if resolved else None,
            # De todos los casos, cuántos se derivaron a una persona.
            "tasa_derivacion": escalated / total if total else None,
        },
        "cobro": billing_summary(conn, tenant_id),
        "csat": csat.summary(conn, tenant_id, days=days),
        "latencia": {
            "turnos": latency[0],
            "p50_ms": _round(latency[1]),
            "p95_ms": _round(latency[2]),
            "promedio_ms": {
                "modelo": _round(latency[3]),
                "base": _round(latency[4]),
                "busqueda": _round(latency[5]),
                "herramientas": _round(latency[6]),
            },
        },
        "modelo": {
            "llamadas": model_calls[0],
            "respaldos": model_calls[1],
            "recuperadas": model_calls[2],
            "rechazos": model_calls[3],
        },
        "errores": {
            "herramientas_fallidas": tool_failures[0],
            "turnos_cortados": [
                {"tipo": kind, "cantidad": count, "ultimo": last} for kind, count, last in errors
            ],
        },
        "por_dia": [
            {
                "dia": day,
                "llamadas": calls,
                "tokens_entrada": int(tokens_in or 0),
                "tokens_salida": int(tokens_out or 0),
                "costo_usd": cost or 0,
            }
            for day, calls, tokens_in, tokens_out, cost in by_day
        ],
        "por_modelo": [
            {
                "modelo": model,
                "uso": kind,
                "llamadas": calls,
                "tokens_entrada": int(tokens_in or 0),
                "tokens_salida": int(tokens_out or 0),
                "costo_usd": cost or 0,
            }
            for model, kind, calls, tokens_in, tokens_out, cost in by_model
        ],
        "por_caso": [
            {
                "caso": case_id,
                "llamadas": calls,
                "tokens": int(tokens or 0),
                "costo_usd": cost or 0,
                "ultimo": last,
            }
            for case_id, calls, tokens, cost, last in by_case
        ],
        "costo_por_caso": {"promedio_usd": case_cost[0] or 0, "maximo_usd": case_cost[1] or 0},
        "evals": [
            {
                "fecha": created,
                "modo": mode or "",
                "prompt": prompt or "",
                "modelo": model or agent_version,
                "resolucion": (scores or {}).get("resolution_rate"),
                "juez": (scores or {}).get("judge_mean"),
                "criticas": (scores or {}).get("critical_failures"),
                "casos": (scores or {}).get("cases"),
            }
            for created, mode, prompt, model, agent_version, scores in evals
        ],
    }


def _round(value: Any) -> int | None:
    return round(float(value)) if value is not None else None


def record_eval_run(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    mode: str,
    prompt_version: str,
    model: str,
    scores: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    """Guarda una corrida de evals en el historial de la empresa (`eval_runs`)."""
    from psycopg.types.json import Jsonb

    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into eval_runs (tenant_id, agent_version, results, scores, mode,"
            " prompt_version, model) values (%s, %s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                f"{prompt_version} · {model}",
                Jsonb(results),
                Jsonb(scores),
                mode,
                prompt_version,
                model,
            ),
        )
