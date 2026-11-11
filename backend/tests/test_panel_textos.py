"""Nombres legibles de los tipos de caso y de las acciones en el panel."""

from app.panel.labels import action_label, type_label


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


def test_las_aprobaciones_se_leen_sin_identificadores() -> None:
    """Etapa 0 del hito 6: "Reembolso · Pedido 1005 · $35.000" en vez de iniciar_reembolso,
    amount y order_id."""
    # jsonb devuelve las claves en otro orden: el pedido va siempre primero y el monto al final.
    refund = action_label("iniciar_reembolso", {"amount": 35000, "order_id": "1005"})
    assert refund == "Reembolso · Pedido 1005 · $35.000"
    assert action_label("cancelar_pedido", {"order_id": "1004"}) == "Cancelación · Pedido 1004"
    address = action_label("actualizar_direccion", {"order_id": "1002", "address": "Av. Maipú 1"})
    assert address == "Cambio de dirección · Pedido 1002 · Dirección: Av. Maipú 1"


def test_una_herramienta_generada_tambien_se_lee_bien() -> None:
    label = action_label("crear_nota_de_credito", {"invoice_id": "F-77", "monto": 1200.75})
    assert label == "Nota de crédito · Invoice id: F-77 · $1.201"
    assert action_label("reenviar_factura", {}) == "Reenviar factura"
