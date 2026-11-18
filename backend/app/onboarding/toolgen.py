"""Tarea 18 — generación de herramientas desde el OpenAPI del cliente (spec, etapa 5).

El modelo elige qué operaciones necesita cada tipo de caso descubierto; el manifiesto lo arma el
código a partir de la operación, así el esquema, el riesgo y los controles no dependen de lo que
escriba el modelo (decisión 021):

* `input_schema`: parámetros de ruta y de query y el cuerpo, estricto (sin campos extra).
* Riesgo: consultar (GET) es bajo; mover dinero (reembolsos, pagos, montos) es alto y siempre
  pide aprobación humana; cancelar o borrar es medio; el resto de las escrituras, bajo.
* Si la operación actúa sobre un recurso que tiene dueño (un campo de teléfono del cliente en
  la respuesta del GET del recurso), pide contacto verificado y control de dueño.
* Las acciones con efecto (POST) llevan clave de idempotencia por caso y recurso.

Las herramientas se guardan sin aprobar: ninguna queda activa sin el cliente (spec).
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg
import yaml
from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope
from app.tools.manifest import MANIFEST_SCHEMA
from app.tools.runtime import HttpClient, ToolRuntime

MONEY = re.compile(r"refund|reembols|payment|pago|amount|monto|charge", re.I)
CANCEL = re.compile(r"cancel", re.I)
OWNER_FIELDS = ("customer_phone", "phone", "telefono")
# Operaciones que no son del agente: buscar clientes por datos personales es de la verificación
# de identidad, no algo que el modelo deba poder llamar.
EXCLUDED = re.compile(r"customers?|clientes?|users?|usuarios?", re.I)


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str
    path: str
    summary: str
    parameters: list[dict[str, Any]] = field(default_factory=list)
    body_schema: dict[str, Any] | None = None
    responses: dict[str, Any] = field(default_factory=dict)


class OperationPicker(Protocol):
    def pick_operations(
        self, case_types: list[tuple[str, str]], operations: list[tuple[str, str]]
    ) -> dict[str, list[str]]: ...


def _resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target: Any = spec
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def operations(spec: dict[str, Any]) -> list[Operation]:
    found = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            body = None
            content = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
            if content:
                body = _resolve(spec, content["schema"])
            found.append(
                Operation(
                    operation_id=op.get("operationId") or f"{method}_{path}",
                    method=method.upper(),
                    path=path,
                    summary=op.get("summary", ""),
                    parameters=[_resolve(spec, p) for p in op.get("parameters") or []],
                    body_schema=body,
                    responses=op.get("responses") or {},
                )
            )
    return found


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).replace("-", "_").lower()


def _input_schema(op: Operation) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in op.parameters:
        if param.get("in") not in ("path", "query"):
            continue  # los encabezados (p. ej. Idempotency-Key) los pone el runtime
        properties[param["name"]] = dict(param.get("schema") or {"type": "string"})
        if param.get("description"):  # el modelo lee para qué sirve cada parámetro
            properties[param["name"]].setdefault("description", param["description"])
        if param.get("required") or param.get("in") == "path":
            required.append(param["name"])
    if op.body_schema:
        for name, schema in (op.body_schema.get("properties") or {}).items():
            properties[name] = dict(schema)
        required.extend(op.body_schema.get("required") or [])
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _resource(spec: dict[str, Any], op: Operation) -> tuple[str, str, str] | None:
    """(nombre del recurso, ruta de su GET, campo de dueño) si el recurso tiene dueño."""
    match = re.match(r"^(/[^/]+/\{[^}]+\})", op.path)
    if not match:
        return None
    resource_path = match.group(1)
    getter = (spec.get("paths", {}).get(resource_path) or {}).get("get")
    if not getter:
        return None
    ok = (getter.get("responses") or {}).get("200") or {}
    content = (_resolve(spec, ok).get("content") or {}).get("application/json") or {}
    schema = _resolve(spec, content.get("schema") or {})
    owner = next((f for f in OWNER_FIELDS if f in (schema.get("properties") or {})), None)
    if owner is None:
        return None
    name = resource_path.split("/")[1].rstrip("s")
    return name, resource_path, owner


def build_manifest(spec: dict[str, Any], op: Operation) -> dict[str, Any]:
    schema = _input_schema(op)
    text = f"{op.path} {op.operation_id} {' '.join(schema['properties'])}"
    if op.method == "GET":
        risk = "low"
    elif MONEY.search(text):
        risk = "high"
    elif CANCEL.search(text) or op.method == "DELETE":
        risk = "medium"
    else:
        risk = "low"
    resource = _resource(spec, op)
    path_params = [p["name"] for p in op.parameters if p.get("in") == "path"]
    manifest: dict[str, Any] = {
        "name": _snake(op.operation_id),
        "description": op.summary or op.operation_id,
        "input_schema": schema,
        "risk": risk,
        "requires_verified_contact": resource is not None,
        "max_amount": None,
        "approval_over": 0 if risk == "high" else None,
        "owner_check": f"{resource[0]}.{resource[2]} == contact.phone" if resource else None,
        "upstream": {"method": op.method, "path": op.path},
        "idempotency_key": (
            f"case_id + {path_params[0]}" if op.method == "POST" and path_params else None
        ),
    }
    if resource:
        manifest["owner_lookup"] = {"method": "GET", "path": resource[1]}
    if "amount" in schema["properties"]:
        manifest["amount_from"] = "input.amount"
    return manifest


def build_manifests(spec: dict[str, Any], picked: dict[str, list[str]]) -> list[dict[str, Any]]:
    by_id = {op.operation_id: op for op in operations(spec)}
    chosen = sorted({op for ops in picked.values() for op in ops if op in by_id})
    validator = Draft202012Validator(MANIFEST_SCHEMA)
    manifests = []
    for op_id in chosen:
        op = by_id[op_id]
        if EXCLUDED.search(op.path.split("/")[1]):
            continue
        manifest = build_manifest(spec, op)
        validator.validate(manifest)
        manifests.append(manifest)
    return manifests


@dataclass(frozen=True)
class ContractResult:
    ok: bool
    detail: str


def _sample(schema: dict[str, Any], name: str, examples: dict[str, Any]) -> Any:
    if name in examples:
        return examples[name]
    kind = schema.get("type")
    return {"integer": 1, "number": 1.0, "boolean": True}.get(str(kind), "ejemplo")


def contract_check(
    spec: dict[str, Any], manifest: dict[str, Any], http: HttpClient, examples: dict[str, Any]
) -> ContractResult:
    """Llama la herramienta contra la API (simulada) y valida que la respuesta sea una de las
    documentadas y cumpla su esquema."""
    schema = manifest["input_schema"]
    params = {name: _sample(s, name, examples) for name, s in schema["properties"].items()}
    errors = sorted(Draft202012Validator(schema).iter_errors(params), key=str)
    if errors:
        return ContractResult(False, f"entrada de prueba inválida: {errors[0].message}")
    upstream = manifest["upstream"]
    key = "contrato:prueba" if manifest["idempotency_key"] else None
    result = ToolRuntime(http).call(upstream["method"], upstream["path"], params, key=key)
    op = spec["paths"][upstream["path"]][upstream["method"].lower()]
    documented = op.get("responses") or {}
    if str(result.status_code) not in documented:
        return ContractResult(False, f"respuesta {result.status_code} no documentada")
    response = _resolve(spec, documented[str(result.status_code)])
    content = (response.get("content") or {}).get("application/json")
    if content:
        body_schema = {**content["schema"], "components": spec.get("components", {})}
        problems = sorted(Draft202012Validator(body_schema).iter_errors(result.data), key=str)
        if problems:
            return ContractResult(False, f"respuesta fuera de contrato: {problems[0].message}")
    return ContractResult(True, f"{result.status_code} según contrato")


REVIEWED_LIMITS = ("max_amount", "approval_over", "amount_from", "alternatives", "retention")


def store_tools(
    conn: psycopg.Connection, tenant_id: uuid.UUID, manifests: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Guarda las herramientas generadas, sin aprobar. Si una ya existía, se actualiza su
    contrato y se conservan su aprobación y los límites que fijó la empresa al revisarla."""
    stored = []
    with tenant_scope(conn, tenant_id):
        for generated in manifests:
            row = conn.execute(
                "select manifest from tools where name = %s for update", (generated["name"],)
            ).fetchone()
            m = dict(generated)
            if row is not None:
                for key in REVIEWED_LIMITS:
                    if row[0].get(key) is not None:
                        m[key] = row[0][key]
                if m["risk"] == "high":
                    m["approval_over"] = 0  # riesgo alto: siempre con aprobación
            conn.execute(
                "insert into tools (tenant_id, name, input_schema, manifest, risk, max_amount,"
                " approved) values (%s, %s, %s, %s, %s, %s, false)"
                " on conflict (tenant_id, name) do update set manifest = excluded.manifest,"
                " input_schema = excluded.input_schema, risk = excluded.risk,"
                " max_amount = excluded.max_amount",
                (
                    tenant_id,
                    m["name"],
                    Jsonb(m["input_schema"]),
                    Jsonb(m),
                    m["risk"],
                    m["max_amount"],
                ),
            )
            stored.append(m)
    return stored


def generate_tools(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    model: OperationPicker,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Elige operaciones por tipo de caso, guarda las herramientas sin aprobar y agrega a cada
    tipo de caso las suyas en sus instrucciones."""
    with tenant_scope(conn, tenant_id):
        types = conn.execute("select id, name, instructions from case_types").fetchall()
    goals = [
        (name, str((yaml.safe_load(instr or "") or {}).get("objetivo", name)))
        for _, name, instr in types
    ]
    candidates = [op for op in operations(spec) if not EXCLUDED.search(op.path.split("/")[1])]
    picked = model.pick_operations(
        goals, [(op.operation_id, f"{op.method} {op.path}: {op.summary}") for op in candidates]
    )
    manifests = store_tools(conn, tenant_id, build_manifests(spec, picked))
    names = {op.operation_id: _snake(op.operation_id) for op in candidates}
    with tenant_scope(conn, tenant_id):
        valid = {m["name"] for m in manifests}
        for type_id, name, instructions in types:
            parsed = yaml.safe_load(instructions or "") or {}
            parsed["herramientas"] = [
                names[op] for op in picked.get(name, []) if names.get(op) in valid
            ]
            conn.execute(
                "update case_types set instructions = %s where id = %s",
                (yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False), type_id),
            )
    return manifests
