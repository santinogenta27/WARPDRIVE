"""El agente habla siempre con voseo rioplatense: el prompt lo pide y, por si el modelo se
escapa al tuteo, las formas de "tú" más comunes se pasan a "vos" antes de enviar."""

import psycopg
from simulated.stack import build_simulated_stack

from app.agent import prompt
from app.agent.voseo import to_voseo


def test_el_cliente_recibe_voseo_aunque_el_modelo_tutee(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="tuteo")
    result = stack.send("lucia", "¿Abren los domingos?")
    assert "Decime si necesitás algo más." in result.message
    assert "Dime" not in result.message
    assert stack.case_message_roles(result.case_id) == ["customer", "agent"]


def test_el_prompt_pide_voseo_y_prohibe_el_tuteo() -> None:
    text = prompt.system_prompt("Acme Store", "rioplatense", "nombre: x")
    assert "voseo" in text and '"decime"' in text and "Nunca uses" in text


def test_pasa_a_voseo_las_formas_de_tu_mas_comunes() -> None:
    assert to_voseo("Indícanos tu correo y dime si puedes esperar.") == (
        "Indicanos tu correo y decime si podés esperar."
    )
    assert to_voseo("¿Tienes el comprobante? Si quieres, envíanos una foto.") == (
        "¿Tenés el comprobante? Si querés, envianos una foto."
    )
    assert to_voseo("Cuéntame qué necesitas y te ayudo. Gracias a ti.") == (
        "Contame qué necesitás y te ayudo. Gracias a vos."
    )
    assert to_voseo("Tú decides: ¿eres el titular?") == "Vos decidís: ¿sos el titular?"


def test_no_toca_el_voseo_ni_palabras_que_no_son_tuteo() -> None:
    text = "Podés pasarme el número de pedido. El pedido indica que ya fue despachado."
    assert to_voseo(text) == text
    assert to_voseo("Tu pedido *1005* está en camino.") == "Tu pedido *1005* está en camino."
