"""Reporte de preparación: "qué falta" tiene que nombrar la causa real de cada falla."""

from app.onboarding.report import missing_for


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
