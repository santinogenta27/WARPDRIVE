"""Tarea 19 — reporte de preparación (spec, etapa 6).

Corre los evals generados contra el agente configurado con lo descubierto (tipos de caso,
herramientas y documentos), en un sandbox con las herramientas en modo simulado: responden con
datos armados desde el esquema del OpenAPI, sin tocar la API real del cliente. Para probarlas, los
tipos y herramientas se activan dentro de un savepoint que se revierte al final: el tenant queda
como estaba y el cliente decide qué activar.

Por tipo de caso: volumen, porcentaje que resolvería solo, errores y qué falta para cubrirlo.
"""

import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import psycopg
import yaml

from app.agent.loop import Agent
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.tools.runtime import ToolRuntime, UpstreamResult

SANDBOX_PHONE = "+5490000000000"
OWNER_FIELDS = ("customer_phone", "phone", "telefono")


class SandboxRuntime(ToolRuntime):
    """Herramientas en modo simulado: respuesta 2xx armada desde el esquema de la operación,
    con los parámetros pedidos y el cliente del sandbox como dueño del recurso."""

    def __init__(self, spec: dict[str, Any], owner_phone: str = SANDBOX_PHONE) -> None:
        super().__init__(httpx.Client())  # no se usa: nada sale a la red
        self._spec = spec
        self._owner = owner_phone

    def call(
        self,
        method: str,
        path_template: str,
        params: dict[str, Any],
        *,
        key: str | None = None,
    ) -> UpstreamResult:
        op = (self._spec.get("paths", {}).get(path_template) or {}).get(method.lower()) or {}
        for code, response in sorted((op.get("responses") or {}).items()):
            if code.startswith("2"):
                content = (self._resolve(response).get("content") or {}).get("application/json")
                schema = self._resolve(content["schema"]) if content else {}
                return UpstreamResult(int(code), self._fake(schema, params, "data"))
        return UpstreamResult(404, {"message": "Operación no disponible en el sandbox"})

    def _resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self._spec
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            node = target
        return node

    def _fake(self, schema: dict[str, Any], params: dict[str, Any], name: str) -> Any:
        schema = self._resolve(schema)
        if name in params:
            return params[name]
        if name.endswith("_id") and name.removesuffix("_id") == "order" and "order_id" in params:
            return params["order_id"]
        if name == "id":
            return next((v for k, v in params.items() if k.endswith("_id")), "1")
        if name in OWNER_FIELDS:
            return self._owner
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), "string")
        if "const" in schema:
            return schema["const"]
        if "enum" in schema:
            return schema["enum"][0]
        if kind == "object":
            return {
                key: self._fake(sub, params, key)
                for key, sub in (schema.get("properties") or {}).items()
            }
        if kind == "array":
            return []
        if kind in ("number", "integer"):
            return 1000
        if kind == "boolean":
            return True
        if schema.get("format") == "date":
            return (date.today() + timedelta(days=3)).isoformat()
        if schema.get("format") == "date-time":
            return datetime.now(UTC).isoformat()
        return "dato de prueba"


AgentFactory = Callable[[psycopg.Connection, ToolRuntime], Agent]


def _contact(
    conn: psycopg.Connection, tenant_id: uuid.UUID, phone: str, verified: bool
) -> uuid.UUID:
    row = conn.execute(
        "insert into contacts (tenant_id, phone_hash, verified_until) values"
        " (%s, %s, case when %s then now() + interval '1 day' end)"
        " on conflict (tenant_id, phone_hash) do update set verified_until ="
        " excluded.verified_until returning id",
        (tenant_id, hash_phone(phone), verified),
    ).fetchone()
    assert row is not None
    contact_id: uuid.UUID = row[0]
    return contact_id


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _grade(case: dict[str, Any], action: str, message: str, escalation: str) -> tuple[bool, str]:
    expected = case["expected"]
    valid = expected.get("acciones_validas") or [expected["accion"]]
    if case["must_escalate"] and action != "derivar" and "derivar" in valid:
        return False, f"tenía que derivar y eligió {action}"
    if action not in valid:
        detail = f" ({escalation})" if escalation else ""
        return False, f"esperaba {expected['accion']} y eligió {action}{detail}"
    leaked = [w for w in expected.get("texto_excluye") or [] if _norm(w) in _norm(message)]
    if leaked:
        return False, f"mostró datos que no debía: {leaked}"
    return True, ""


def _missing(name: str, tools: list[str], system_share: float, failures: list[str]) -> list[str]:
    missing = []
    if system_share >= 0.5 and not tools:
        missing.append("Una herramienta para actuar en el sistema (hoy solo puede informar)")
    if sum("fundamento" in f for f in failures) >= max(1, len(failures) // 2) and failures:
        missing.append("Documentos que respalden las respuestas (deriva por falta de fuente)")
    if any("no está activo" in f or "esperaba" in f and "derivar" in f for f in failures):
        missing.append("Revisar el nombre y el objetivo del tipo: el clasificador no lo reconoce")
    return missing


def readiness_report(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    spec: dict[str, Any],
    agent_factory: AgentFactory,
) -> dict[str, Any]:
    runtime = SandboxRuntime(spec)
    outcomes: list[dict[str, Any]] = []
    with tenant_scope(conn, tenant_id):
        types = conn.execute(
            "select id, name, volume, examples, instructions from case_types order by volume desc"
        ).fetchall()
        cases = conn.execute(
            "select id, case_type_id, input, expected, must_escalate, origin from eval_cases"
            " order by created_at, id"
        ).fetchall()
        total_conversations = conn.execute("select count(*) from conversations").fetchone()
        unassigned = conn.execute(
            "select count(*) from conversations where case_type_id is null"
        ).fetchone()
    try:
        with conn.transaction():  # savepoint: todo lo del sandbox se revierte al final
            with tenant_scope(conn, tenant_id):
                conn.execute("update case_types set active = true")
                conn.execute("update tools set approved = true")
                verified = _contact(conn, tenant_id, SANDBOX_PHONE, True)
                unverified = _contact(conn, tenant_id, "+5490000000001", False)
            agent = agent_factory(conn, runtime)
            for case_id, type_id, input_, expected, must_escalate, origin in cases:
                contact = verified if input_.get("contacto") == "verificado" else unverified
                result = agent.handle_message(tenant_id, contact, input_["mensaje"])
                escalation = str((result.escalation_summary or {}).get("motivo") or "")
                ok, why = _grade(
                    {"expected": expected, "must_escalate": must_escalate},
                    result.action,
                    result.message,
                    escalation,
                )
                outcomes.append(
                    {
                        "eval": str(case_id),
                        "tipo": type_id,
                        "origen": origin,
                        "mensaje": input_["mensaje"],
                        "accion": result.action,
                        "ok": ok,
                        "motivo": why,
                    }
                )
                with tenant_scope(conn, tenant_id):
                    conn.execute(
                        "update cases set status = 'resolved' where id = %s", (result.case_id,)
                    )
            raise psycopg.Rollback()
    except psycopg.Rollback:
        pass

    per_type = []
    for type_id, name, volume, examples, instructions in types:
        mine = [o for o in outcomes if o["tipo"] == type_id]
        failures = [o["motivo"] for o in mine if not o["ok"]]
        share = (
            sum(bool(e.get("accion_en_sistema")) for e in examples) / len(examples)
            if examples
            else 0.0
        )
        parsed = yaml.safe_load(instructions or "") or {}
        tools = list(parsed.get("herramientas") or [])
        resolved = sum(o["ok"] for o in mine)
        per_type.append(
            {
                "nombre": name,
                "objetivo": parsed.get("objetivo", ""),
                "volumen": volume,
                "herramientas": tools,
                "evals": len(mine),
                "resueltos": resolved,
                "porcentaje": round(resolved / len(mine), 4) if mine else 0.0,
                "errores": [
                    {"mensaje": o["mensaje"], "motivo": o["motivo"]} for o in mine if not o["ok"]
                ],
                "falta": _missing(name, tools, share, failures),
            }
        )
    adversarial = [o for o in outcomes if o["origen"] == "adversarial"]
    covered = sum(t["volumen"] for t in per_type)
    estimate = (sum(t["volumen"] * t["porcentaje"] for t in per_type) / covered) if covered else 0.0
    total = int(total_conversations[0]) if total_conversations else 0
    return {
        "generado": datetime.now(UTC).isoformat(timespec="seconds"),
        "conversaciones": total,
        "sin_tipo": int(unassigned[0]) if unassigned else 0,
        "resolucion_estimada": round(estimate * covered / total, 4) if total else 0.0,
        "resolucion_en_tipos_detectados": round(estimate, 4),
        "tipos": per_type,
        "adversariales": {
            "total": len(adversarial),
            "superados": sum(o["ok"] for o in adversarial),
            "fallas": [
                {"mensaje": o["mensaje"], "motivo": o["motivo"]} for o in adversarial if not o["ok"]
            ],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Reporte de preparación",
        "",
        f"Conversaciones analizadas: {report['conversaciones']} "
        f"({report['sin_tipo']} sin tipo de caso: grupos chicos o poco claros).",
        f"Resolvería solo: **{report['resolucion_estimada']:.0%}** del volumen total "
        f"({report['resolucion_en_tipos_detectados']:.0%} dentro de los tipos detectados).",
        "",
        "| Tipo de caso | Volumen | Evals | Resueltos | % | Herramientas | Qué falta |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in report["tipos"]:
        lines.append(
            f"| {t['nombre']} | {t['volumen']} | {t['evals']} | {t['resueltos']} | "
            f"{t['porcentaje']:.0%} | {', '.join(t['herramientas']) or '—'} | "
            f"{'; '.join(t['falta']) or '—'} |"
        )
    adv = report["adversariales"]
    lines += ["", f"Casos adversariales superados: {adv['superados']} de {adv['total']}."]
    return "\n".join(lines) + "\n"
