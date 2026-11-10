"""Tarea 17 — test de aceptación: cada caso generado valida contra el esquema y referencia una
conversación fuente.

Por cada tipo de caso descubierto, el modelo convierte conversaciones reales resueltas en casos
de prueba. Se agregan casos adversariales (inyección, datos en masa, fuera de política y datos
de otro cliente). Lo que no valida o inventa una conversación fuente se descarta.
"""

import uuid
from typing import Any

import psycopg
from jsonschema import Draft202012Validator

from app.db.connection import tenant_scope
from app.onboarding.discover import instructions_yaml
from app.onboarding.evalgen import (
    ADVERSARIAL,
    EVAL_CASE_SCHEMA,
    expected_from_draft,
    generate_evals,
)


class FakeEvalModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def make_evals(
        self, case_type: str, goal: str, conversations: list[tuple[str, str]]
    ) -> list[dict[str, Any]]:
        self.calls.append(case_type)
        cases: list[dict[str, Any]] = [
            {
                "conversacion": cid,
                "mensaje": text.splitlines()[0].split(": ", 1)[1],
                "accion": "ejecutar",
                # Al mensaje le puede faltar un dato para actuar: preguntar también es correcto.
                "acciones_validas": ["responder", "volar"],
                "debe_escalar": False,
                "palabras_clave": ["pedido"],
            }
            for cid, text in conversations
        ]
        cases.append(
            {
                "conversacion": "inventada-999",
                "mensaje": "hola",
                "accion": "responder",
                "debe_escalar": False,
                "palabras_clave": [],
            }
        )  # fuente inexistente
        cases.append({"conversacion": conversations[0][0], "accion": "volar"})  # no valida
        return cases


def _tenant_with_history(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute("insert into tenants (name) values ('Evals') returning id").fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    with tenant_scope(conn, tenant_id):
        for name in ("donde_esta_mi_pedido", "cambio_de_talle"):
            type_row = conn.execute(
                "insert into case_types (tenant_id, name, volume, instructions)"
                " values (%s, %s, 4, %s) returning id",
                (tenant_id, name, instructions_yaml(name, f"Resolver {name}")),
            ).fetchone()
            assert type_row is not None
            for i in range(4):
                conv = conn.execute(
                    "insert into conversations (tenant_id, external_id, case_type_id)"
                    " values (%s, %s, %s) returning id",
                    (tenant_id, f"{name}-{i}", type_row[0]),
                ).fetchone()
                assert conv is not None
                for role, text in (
                    ("customer", f"consulta {i} sobre el pedido 1{i}23"),
                    ("agent", "Listo, ya lo revisé."),
                    ("customer", "gracias"),
                ):
                    conn.execute(
                        "insert into messages (tenant_id, conversation_id, role, text)"
                        " values (%s, %s, %s, %s)",
                        (tenant_id, conv[0], role, text),
                    )
    return tenant_id


def test_cada_caso_generado_valida_y_referencia_una_conversacion_fuente(
    conn: psycopg.Connection,
) -> None:
    tenant_id = _tenant_with_history(conn)
    model = FakeEvalModel()
    created = generate_evals(conn, tenant_id, model, per_type=3)

    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select e.input, e.expected, e.must_escalate, e.origin, t.name from eval_cases e"
            " left join case_types t on t.id = e.case_type_id"
        ).fetchall()
        sources = {r[0] for r in conn.execute("select external_id from conversations").fetchall()}
    assert len(rows) == created == 2 * 3 + len(ADVERSARIAL)
    validator = Draft202012Validator(EVAL_CASE_SCHEMA)
    for input_, expected, must_escalate, origin, type_name in rows:
        case = {
            "mensaje": input_["mensaje"],
            "contacto": input_["contacto"],
            "esperado": expected,
            "origen": origin,
        }
        validator.validate(case)
        assert origin == "adversarial" or origin in sources
        assert must_escalate == expected["debe_escalar"]
        if origin != "adversarial":
            # Las alternativas del modelo se guardan filtradas, con la acción principal incluida.
            assert expected["acciones_validas"] == ["ejecutar", "responder"]
        assert (type_name is None) == (origin == "adversarial")
    kinds = {r[1].get("categoria") for r in rows if r[3] == "adversarial"}
    assert {"inyeccion", "datos_en_masa", "fuera_de_politica", "datos_de_otro_cliente"} <= kinds

    # Un reembolso necesita aprobación humana, pero antes el agente puede pedir lo que falta
    # (una foto del producto fallado, una confirmación): responder también es correcto.
    refund = expected_from_draft(
        {"accion": "pedir_aprobacion", "debe_escalar": False, "palabras_clave": ["plata"]}
    )
    assert refund["acciones_validas"] == ["pedir_aprobacion", "responder"]
    plain = expected_from_draft({"accion": "responder", "debe_escalar": False, "palabras_clave": []})
    assert "acciones_validas" not in plain

    # Idempotente: los tipos que ya tienen casos no se vuelven a pedir al modelo.
    model.calls.clear()
    assert generate_evals(conn, tenant_id, model, per_type=3) == 0
    assert model.calls == []
