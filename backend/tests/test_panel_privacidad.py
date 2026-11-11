"""El resumen de un caso en el panel nunca muestra códigos ni datos personales."""

from app.panel.privacy import mask_codes, redact, summary


def test_el_resumen_no_muestra_codigos_ni_datos_personales() -> None:
    assert redact("131841") == "[código]"
    assert redact("martin@example.com") == "[email]"
    assert redact("mi cel es 11 5555-0202 y mi DNI 30.123.456") == (
        "mi cel es [teléfono] y mi DNI [DNI]"
    )
    assert redact("Soy Martín Ibarra, quiero el reembolso del 1005") == (
        "Soy [nombre], quiero el reembolso del 1005"
    )
    assert redact("pagué $150.000 por el pedido 1005") == "pagué $150.000 por el pedido 1005"


def test_el_resumen_es_el_primer_mensaje_con_contenido_del_cliente() -> None:
    messages = ["Quiero el reembolso del pedido 1005", "martin@example.com", "131841"]
    assert summary(messages) == "Quiero el reembolso del pedido 1005"
    assert summary(["martin@example.com", "¿Dónde está mi pedido?"]) == "¿Dónde está mi pedido?"
    assert summary(["131841"]) == "[código]"
    assert summary([]) == ""


def test_el_detalle_tampoco_muestra_codigos() -> None:
    assert mask_codes("921074.") == "[código]."
    assert mask_codes("Tu pedido 1005 por $35.000") == "Tu pedido 1005 por $35.000"
