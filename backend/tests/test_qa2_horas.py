"""Hito QA 2 — test de aceptación: horas en 12 y en 24 (decisión 093).

Con el modelo real y Acme en inglés, "Our branches are open from 10 a.m. to 8 p.m." se rechazaba
dos veces porque el documento dice "de 10 a 20 hs" y el control de fundamento no veía el 8; la
pregunta terminaba derivada.
"""

from app.agent.grounding import check_grounding

HOURS_ES = {"horarios": "Las sucursales abren de lunes a sábado de 10 a 20 hs."}
HOURS_EN = {"hours": "Our stores are open Monday to Saturday from 9 a.m. to 6:30 p.m."}


def _check(message: str, documents: dict[str, str], language: str) -> bool:
    return check_grounding(
        message, list(documents), True, documents, [], False, language=language
    ).ok


def test_la_hora_de_24_dicha_en_12_tiene_respaldo() -> None:
    message = "Our branches are open Monday through Saturday, 10 a.m. to 8 p.m."
    assert _check(message, HOURS_ES, "en")
    assert _check("We close at 8pm and open at 10 AM.", HOURS_ES, "en")


def test_la_hora_de_12_dicha_en_24_tiene_respaldo() -> None:
    assert _check("Abrimos de 9 a 18:30 horas.", HOURS_EN, "es")


def test_una_hora_inventada_se_sigue_rechazando() -> None:
    assert not _check("We're open until 9 p.m.", HOURS_ES, "en")
    assert not _check("We open at 7 a.m.", HOURS_ES, "en")
    assert not _check("Cerramos a las 21 horas.", HOURS_EN, "es")


ALWAYS = {"atencion": "El asistente virtual responde por WhatsApp las 24 horas, todos los días."}


def test_24_7_con_respaldo_de_24_horas_todos_los_dias() -> None:
    assert _check("Our virtual assistant answers on WhatsApp 24/7.", ALWAYS, "en")
    assert not _check("Our team answers 24/7.", {"x": "Atendemos las 24 horas hábiles."}, "en")
