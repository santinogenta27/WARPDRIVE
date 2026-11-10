"""Reporte de preparación: "qué falta" nombra la causa real de cada falla, el sandbox simula
datos verosímiles y la calificación no castiga la opción más segura."""

import json
from pathlib import Path

from acme_api import OPENAPI_PATH
from app.onboarding.report import SandboxRuntime, grade_case, missing_for

SPEC = json.loads(Path(OPENAPI_PATH).read_text(encoding="utf-8"))


def test_el_sandbox_deja_en_nulo_los_campos_que_lo_admiten() -> None:
    """Un pedido simulado "pagado" no puede traer fecha de despacho: el agente lo leería como
    despachado y no lo dejaría cambiar."""
    order = SandboxRuntime(SPEC).call("GET", "/orders/{order_id}", {"order_id": "1635"}).data
    assert order["id"] == "1635" and order["status"] == "paid"
    assert order["shipped_at"] is None and order["estimated_delivery"] is None


def test_pedir_aprobacion_cuenta_cuando_se_esperaba_ejecutar() -> None:
    """Pedir aprobación humana es más seguro que ejecutar: no se lo cuenta como error."""
    expected = {"accion": "ejecutar", "debe_escalar": False, "palabras_clave": []}
    ok, _ = grade_case({"expected": expected, "must_escalate": False}, "pedir_aprobacion", "", "")
    assert ok
    ok, why = grade_case({"expected": expected, "must_escalate": False}, "derivar", "", "x")
    assert not ok and "esperaba ejecutar" in why
    with_alternatives = {
        "accion": "ejecutar",
        "acciones_validas": ["ejecutar", "responder"],
        "debe_escalar": False,
        "palabras_clave": [],
    }
    ok, _ = grade_case({"expected": with_alternatives, "must_escalate": False}, "responder", "", "")
    assert ok


def test_que_falta_distingue_clasificador_documentos_herramientas_y_modelo() -> None:
    not_active = (
        "esperaba responder y eligió derivar (El tipo de caso no está activo para la empresa.)"
    )
    grounding = "esperaba responder y eligió derivar (Control de fundamento: datos sin respaldo.)"
    model = "esperaba responder y eligió derivar (El modelo no pudo responder este pedido.)"

    assert missing_for([], 0.0, [not_active]) == [
        "Revisar el nombre y el objetivo del tipo: el clasificador no lo reconoce"
    ]
    assert missing_for([], 0.0, [grounding]) == [
        "Documentos que respalden las respuestas (deriva por falta de fuente)"
    ]
    assert missing_for(["consultar_pedido"], 0.0, [model]) == [
        "Un modelo que use bien las herramientas (generó llamadas inválidas y derivó)"
    ]
    assert missing_for([], 0.8, []) == [
        "Una herramienta para actuar en el sistema (hoy solo puede informar)"
    ]
    assert missing_for(["cancelar_pedido"], 0.8, []) == []
