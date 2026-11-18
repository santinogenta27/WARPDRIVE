"""Hito 9, tarea 5 — test de aceptación: tono y marca (decisión 052).

Nombre, color, logo, sugerencias e idioma ya se editaban (decisiones 031 y 036). Esta tarea suma
el nombre del asistente y su saludo: los ve el cliente al abrir el chat y el asistente se
presenta con ese nombre.
"""

import uuid

import psycopg
import pytest

from app.channels.web import widget
from app.companies.create import create_company

NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"


def _norte(conn: psycopg.Connection) -> uuid.UUID:
    return create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD).tenant_id


def test_sin_saludo_propio_usa_el_de_siempre(conn: psycopg.Connection) -> None:
    tenant = _norte(conn)
    found = widget.load(conn, tenant)
    assert found.assistant == "" and found.greeting == ""
    assert widget.greeting_for(found) == widget.greeting("Tienda Norte", "es")


def test_el_saludo_y_el_nombre_del_asistente_se_guardan_y_se_muestran(
    conn: psycopg.Connection,
) -> None:
    tenant = _norte(conn)
    widget.save(
        conn,
        tenant,
        name="Tienda Norte",
        accent="#0f766e",
        domains="",
        suggestions="¿Dónde está mi pedido?",
        assistant="Nina",
        greeting="¡Hola! Soy Nina, de Tienda Norte. ¿Te ayudo con tu compra?",
    )
    found = widget.load(conn, tenant)
    assert found.assistant == "Nina"
    assert widget.greeting_for(found).startswith("¡Hola! Soy Nina")
    assert widget.assistant_name(conn, tenant) == "Nina"


@pytest.mark.parametrize(
    ("assistant", "greeting"),
    [("N", ""), ("x" * 41, ""), ("Nina", "x" * 301), ("Nina", "<script>alert(1)</script>")],
)
def test_valida_el_nombre_y_el_saludo(
    conn: psycopg.Connection, assistant: str, greeting: str
) -> None:
    with pytest.raises(widget.WidgetError):
        widget.save(
            conn,
            _norte(conn),
            name="Tienda Norte",
            accent="#0f766e",
            domains="",
            suggestions="",
            assistant=assistant,
            greeting=greeting,
        )
