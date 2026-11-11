"""La negrita de markdown (**texto**) se convierte a la de WhatsApp (*texto*) antes de enviar."""

from app.channels.whatsapp.format import to_whatsapp


def test_convierte_negrita_de_markdown_a_whatsapp() -> None:
    assert (
        to_whatsapp("Tu pedido **1001** llega el **10/11**.")
        == "Tu pedido *1001* llega el *10/11*."
    )
    assert to_whatsapp("**lunes a sábado de 10 a 20**") == "*lunes a sábado de 10 a 20*"


def test_deja_igual_lo_que_ya_esta_en_formato_whatsapp() -> None:
    for text in ("El pedido *1001* ya salió.", "Sin formato.", "2 ** 3", "**", "a ** b ** c"):
        assert to_whatsapp(text) == text
