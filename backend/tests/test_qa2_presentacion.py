"""Hito QA 2 — test de aceptación: el asistente se presenta una sola vez (decisión 095).

En el recorrido de punta a punta con el modelo real, el asistente con nombre ("Sunny") arrancaba
cada respuesta con "Hello, I am Sunny, the virtual assistant…", también en medio de la
conversación y después de verificar al cliente.
"""

from app.agent.prompt import system_prompt


def test_en_espanol_se_presenta_solo_en_la_primera_respuesta() -> None:
    text = system_prompt("Tienda", "rioplatense", "", "Luna")
    assert "Te llamás Luna." in text
    assert "solo en tu primera respuesta" in text and "no repitas tu nombre" in text


def test_en_ingles_tambien() -> None:
    text = system_prompt("Store", "english", "", "Sunny")
    assert "Your name is Sunny." in text
    assert "only in your first reply" in text and "don't repeat your name" in text


def test_sin_nombre_no_cambia_nada() -> None:
    assert "Te llamás" not in system_prompt("Tienda", "rioplatense", "")
