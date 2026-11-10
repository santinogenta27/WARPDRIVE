"""Nombres legibles de los tipos de caso en el panel."""

from app.panel.labels import type_label


def test_los_tipos_de_caso_se_muestran_con_nombre_legible() -> None:
    assert type_label("donde_esta_mi_pedido") == "Dónde está mi pedido"
    assert type_label("cambio_direccion") == "Cambio dirección"
    assert type_label("medios_de_pago_y_cuotas") == "Medios de pago y cuotas"
    assert type_label("facturacion") == "Facturación"


def test_la_charla_y_las_etiquetas_especiales_tienen_nombre_propio() -> None:
    assert type_label("conversacion_general") == "Conversación general"
    assert type_label("consulta_general") == "Consulta general"
    assert type_label("derivar_humano") == "Pidió hablar con una persona"
    assert type_label(None) == "Sin clasificar"
