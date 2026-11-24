"""Demo en inglés, cambio D — test de aceptación del aviso de aprobación en el panel.

Cuando un reembolso queda "Waiting for approval", se nota sin buscarlo: un contador en el menú
y la fila de la solicitud resaltada en Casos, con Approve y Reject a la vista. Al decidir, una
confirmación breve en pantalla ("Refund approved · confirmation email sent").
"""

import re
from collections.abc import Iterator

import psycopg
import pytest
from test_email_reembolso import World, _refund_waiting, _world


@pytest.fixture
def northwind(conn: psycopg.Connection) -> Iterator[World]:
    yield from _world(conn, "northwind")


@pytest.fixture
def acme(conn: psycopg.Connection) -> Iterator[World]:
    yield from _world(conn, "acme")


def _badge(page: str) -> str | None:
    """El contador del menú junto a Inbox, si está (decisión 085)."""
    found = re.search(
        r'href="/panel/inbox"[^>]*>(?:(?!</a>).)*?<span class="count"[^>]*>(\d+)<', page, re.S
    )
    return found.group(1) if found else None


def _attention(page: str) -> str:
    """La fila resaltada de Casos (la que espera una aprobación)."""
    found = re.search(r'<div class="card row attention".*?</form></div>', page, re.S)
    assert found is not None, "no hay una fila resaltada"
    return found.group(0)


def _csrf(page: str) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    assert found is not None
    return found.group(1)


def test_el_reembolso_en_espera_se_ve_en_el_menu_y_en_casos(northwind: World) -> None:
    panel = northwind.panel
    assert _badge(panel.get("/panel/inbox").text) is None  # nada pendiente: sin contador
    _refund_waiting(northwind.stack, "I'd like a refund for order NW-4528.", "emily@example.com")

    cases = panel.get("/panel/inbox").text
    assert _badge(cases) == "1"
    assert 'aria-label="1 waiting for approval"' in cases
    row = _attention(cases)
    assert "Waiting for approval" in row and "Refund · Order NW-4528 · $213" in row
    assert ">Approve<" in row and ">Reject<" in row
    assert 'name="volver" value="/panel/inbox"' in row  # al decidir, vuelve a Casos
    # La misma solicitud, resaltada en Aprobaciones, y el contador en cada pantalla.
    approvals = panel.get("/panel/inbox/approvals").text
    assert 'class="card attention"' in approvals and _badge(approvals) == "1"
    assert _badge(panel.get("/panel/insights").text) == "1"


def test_al_aprobar_una_confirmacion_breve_y_el_contador_se_va(northwind: World) -> None:
    panel = northwind.panel
    pending = _refund_waiting(
        northwind.stack, "I'd like a refund for order NW-4528.", "emily@example.com"
    )
    cases = panel.get("/panel/inbox").text
    done = panel.post(
        f"/panel/aprobaciones/{pending.approval_id}",
        data={"decision": "aprobar", "csrf": _csrf(cases), "volver": "/panel/inbox"},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/panel/inbox?aviso=reembolso-email"
    after = panel.get(done.headers["location"]).text
    assert "Refund approved · confirmation email sent" in after
    assert 'class="toast"' in after and 'role="status"' in after
    assert _badge(after) is None and 'class="card row attention"' not in after


def test_al_rechazar_lo_dice_y_un_aviso_desconocido_no_muestra_nada(northwind: World) -> None:
    panel = northwind.panel
    pending = _refund_waiting(
        northwind.stack, "I'd like a refund for order NW-4528.", "emily@example.com"
    )
    page = panel.get("/panel/inbox/approvals").text
    done = panel.post(
        f"/panel/aprobaciones/{pending.approval_id}",
        data={"decision": "rechazar", "csrf": _csrf(page)},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/panel/inbox/approvals?aviso=rechazado"
    assert "Request rejected" in panel.get(done.headers["location"]).text
    # El texto del aviso sale de una lista fija: lo que venga en la URL no se muestra.
    forged = panel.get("/panel/inbox?aviso=<script>alert(1)</script>").text
    assert 'class="toast"' not in forged and "alert(1)" not in forged


def test_en_espanol_para_una_empresa_en_espanol(acme: World) -> None:
    panel = acme.panel
    pending = _refund_waiting(
        acme.stack, "Quiero el reembolso del pedido 1005", "martin@example.com"
    )
    cases = panel.get("/panel/inbox").text
    assert _badge(cases) == "1" and 'aria-label="1 esperando aprobación"' in cases
    row = _attention(cases)
    assert ">Aprobar<" in row and ">Rechazar<" in row and "Pedido 1005" in row
    done = panel.post(
        f"/panel/aprobaciones/{pending.approval_id}",
        data={"decision": "aprobar", "csrf": _csrf(cases), "volver": "/panel/inbox"},
        follow_redirects=False,
    )
    after = panel.get(done.headers["location"]).text
    assert "Reembolso aprobado · se envió el email de confirmación" in after
