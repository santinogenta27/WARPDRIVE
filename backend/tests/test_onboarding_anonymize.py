"""Tarea 15 — test de aceptación: un corpus con datos personales sembrados sale con 0 coincidencias.

La anonimización reemplaza nombres, teléfonos, emails, DNI/CUIT y tarjetas por marcadores tipados
(<PHONE_1>) antes de que cualquier dato llegue a un modelo. Los números de pedido, montos y
fechas se conservan: hacen falta para descubrir casos y generar evals.
"""

import json
import sys
from pathlib import Path

from app.onboarding.anonymize import Anonymizer, luhn_ok

ONBOARDING = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "acme" / "onboarding"
sys.path.insert(0, str(ONBOARDING))
from generate_history import build  # noqa: E402


def test_el_corpus_sembrado_sale_sin_datos_personales() -> None:
    conversations = build()
    seeded = json.loads((ONBOARDING / "seeded_pii.json").read_text(encoding="utf-8"))
    leaks = []
    for conversation in conversations:
        anonymizer = Anonymizer(names=[conversation["customer"]["name"]])
        for message in conversation["messages"]:
            clean = anonymizer.anonymize(message["text"])
            leaks.extend(value for value in seeded if value in clean)
            for part in conversation["customer"]["name"].split():
                if part in clean:
                    leaks.append(part)
    assert leaks == []


def test_usa_marcadores_tipados_y_consistentes_dentro_de_la_conversacion() -> None:
    anonymizer = Anonymizer(names=["Lucía Ferrari"])
    first = anonymizer.anonymize(
        "Hola Lucía Ferrari, te escribo al +54 9 11 5555-0101 o a lucia.ferrari@example.com"
    )
    assert first == "Hola <NAME_1>, te escribo al <PHONE_1> o a <EMAIL_1>"
    again = anonymizer.anonymize("Lucía, confirmo el 11 5555-0101 y el CUIT 27-30123456-4")
    assert again == "<NAME_1>, confirmo el <PHONE_1> y el CUIT <CUIT_1>"


def test_detecta_nombres_que_se_presentan_y_dni_y_tarjetas() -> None:
    anonymizer = Anonymizer()
    text = anonymizer.anonymize(
        "soy Carla Benítez, mi DNI es 30.123.456 y pagué con la 4509 9535 6623 3704"
    )
    assert text == "soy <NAME_1>, mi DNI es <DNI_1> y pagué con la <CARD_1>"
    assert luhn_ok("4509953566233704") and not luhn_ok("4509953566233705")


def test_conserva_pedidos_montos_y_fechas() -> None:
    text = "El pedido 1001 de $45.990 llega el 10/11; despachado el 8/11 a las 14:30."
    assert Anonymizer().anonymize(text) == text
