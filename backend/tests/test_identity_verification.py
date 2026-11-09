"""Verificación de identidad por código al email (spec v1.1, "Verificación de identidad").

Sin verificar, no hay acceso a datos de cuenta. El cliente da el email registrado, recibe un
código de 6 dígitos (interfaz `Mailer`) que vence en 10 minutos, con hasta 3 intentos, y al
confirmarlo el agente responde la consulta que había quedado pendiente. Sofía (contacto no
verificado del fixture de Acme) es la titular del pedido 1006.
"""

import re

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent.loop import VERIFICATION_MESSAGE
from app.db.connection import tenant_scope

QUESTION = "¿Dónde está mi pedido 1006?"


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def _code(stack: SimulatedStack) -> str:
    match = re.search(r"\b(\d{6})\b", stack.mailer.sent[-1].body)
    assert match is not None
    return match.group(1)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _verified(stack: SimulatedStack) -> bool:
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute(
            "select coalesce(verified_until > now(), false) from contacts where id = %s",
            (stack.seed.contacts["sofia"],),
        ).fetchone()
    return bool(row and row[0])


def _consulted(stack: SimulatedStack) -> list[str]:
    return [
        str(call.input.get("order_id"))
        for result in stack.results
        for call in result.tool_calls
        if call.name == "consultar_pedido" and call.status == "executed"
    ]


def test_sin_verificar_no_hay_acceso_a_datos_de_cuenta(stack: SimulatedStack) -> None:
    first = stack.send("sofia", QUESTION)
    assert first.message == VERIFICATION_MESSAGE and first.verification_requested
    assert _consulted(stack) == []

    sent = stack.send("sofia", "Es sofia@example.com")
    assert "código" in sent.message and "1006" not in sent.message
    assert [m.to for m in stack.mailer.sent] == ["sofia@example.com"]
    code = _code(stack)

    wrong = stack.send("sofia", _wrong(code))
    assert "no es correcto" in wrong.message and "2 intentos" in wrong.message
    assert not _verified(stack) and _consulted(stack) == []

    ok = stack.send("sofia", f"El código es {code}")
    assert ok.message.startswith("¡Listo")
    assert _verified(stack)
    assert _consulted(stack) == ["1006"]  # responde la consulta que había quedado pendiente
    assert ok.action == "responder"


def test_un_email_no_registrado_no_revela_nada_ni_verifica(stack: SimulatedStack) -> None:
    stack.send("sofia", QUESTION)
    registered = stack.send("sofia", "sofia@example.com").message
    stack.send("sofia", QUESTION)
    unknown = stack.send("sofia", "nadie@example.com").message
    assert unknown == registered  # misma respuesta: no revela si el email existe
    assert [m.to for m in stack.mailer.sent] == ["sofia@example.com"]
    stack.send("sofia", "123456")
    assert not _verified(stack)


def test_tres_codigos_incorrectos_invalidan_el_codigo(stack: SimulatedStack) -> None:
    stack.send("sofia", QUESTION)
    stack.send("sofia", "sofia@example.com")
    code = _code(stack)
    for _ in range(2):
        stack.send("sofia", _wrong(code))
    last = stack.send("sofia", _wrong(code))
    assert "no quedan intentos" in last.message
    stack.send("sofia", code)  # el código correcto ya no sirve
    assert not _verified(stack) and _consulted(stack) == []


def test_el_codigo_vence_a_los_10_minutos(stack: SimulatedStack) -> None:
    stack.send("sofia", QUESTION)
    stack.send("sofia", "sofia@example.com")
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        expires = stack.conn.execute(
            "select expires_at - now() from identity_verifications where status = 'code_sent'"
        ).fetchone()
        assert expires is not None and 9 * 60 < expires[0].total_seconds() <= 10 * 60
        stack.conn.execute(
            "update identity_verifications set expires_at = now() - interval '1 second'"
        )
    late = stack.send("sofia", _code(stack))
    assert "venció" in late.message
    assert not _verified(stack)

    # Con un email nuevo llega otro código, y ese sí verifica.
    stack.send("sofia", "sofia@example.com")
    stack.send("sofia", _code(stack))
    assert _verified(stack) and _consulted(stack) == ["1006"]


def test_el_codigo_no_se_guarda_en_claro(stack: SimulatedStack) -> None:
    stack.send("sofia", QUESTION)
    stack.send("sofia", "sofia@example.com")
    code = _code(stack)
    with tenant_scope(stack.conn, stack.seed.tenant_id):
        row = stack.conn.execute("select code_hash from identity_verifications").fetchone()
    assert row is not None and code not in row[0]
