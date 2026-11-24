"""Hito QA 2 — test de aceptación: el supervisor ve las fuentes citadas (decisión 092).

Con el modelo real, el supervisor pedía dos veces "citar la fuente" en el texto de una respuesta
correcta y la empresa nueva terminaba derivando todo: las fuentes van aparte, en `fuentes`, y el
supervisor no las veía.
"""

import json

import psycopg
from simulated.stack import build_simulated_stack


def _supervisor_request(requests: list[dict]) -> dict:  # type: ignore[type-arg]
    found = [r["body"] for r in requests if "supervisar" in r["tool_names"]]
    assert found, "el supervisor no revisó la respuesta"
    return found[-1]


def test_el_supervisor_recibe_las_fuentes_citadas_aparte(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn)
    result = stack.send("lucia", "¿Abren los domingos?")
    assert result.sources
    body = _supervisor_request(stack.claude_requests)
    content = json.dumps(body["messages"], ensure_ascii=False)
    assert f"<fuentes_citadas>{', '.join(result.sources)}</fuentes_citadas>" in content
    system = json.dumps(body["system"], ensure_ascii=False)
    assert "Las fuentes se citan aparte" in system


def test_en_ingles_tambien(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, company="northwind")
    stack.send("sophia", "What is your return policy?")
    body = _supervisor_request(stack.claude_requests)
    assert "Sources are cited separately" in json.dumps(body["system"], ensure_ascii=False)
