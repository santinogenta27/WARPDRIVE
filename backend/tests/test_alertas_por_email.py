"""Hito 7, tarea 3 — las alertas de la decisión 028 también le llegan por email al operador.

Siguen quedando en los logs. El email sale por el `Mailer` (Resend, si está configurado), sin
datos personales, sin demorar la respuesta al cliente y sin inundar la casilla. Decisión 033.
"""

import uuid
from decimal import Decimal

import pytest

from app.mailer import ConsoleMailer, Email, MailError
from app.obs import alerts

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
CASE = uuid.UUID("22222222-2222-2222-2222-222222222222")
SLOW = {
    "total_ms": 15000,
    "modelo_ms": 14000,
    "base_ms": 500,
    "busqueda_ms": 300,
    "herramientas_ms": 0,
    "otros_ms": 200,
    "llamadas": {"base": 20, "modelo": 2, "busqueda": 1, "herramientas": 0},
}


class Outbox:
    def __init__(self, failing: bool = False) -> None:
        self.sent: list[Email] = []
        self.failing = failing

    def send(self, email: Email) -> None:
        if self.failing:
            raise MailError("Resend respondió 503 (sin detalle).")
        self.sent.append(email)


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    box = Outbox()
    alerts.reset()
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    monkeypatch.setattr(alerts, "mailer_from_env", lambda: box)
    monkeypatch.setattr(alerts, "BACKGROUND", False)  # en los tests, el envío es en el momento
    return box


def slow_turn() -> list[str]:
    return alerts.check_turn(
        TENANT, CASE, SLOW, case_cost=Decimal("0.01"), turn_cost=Decimal("0.01")
    )


def test_una_alerta_queda_en_los_logs_y_se_envia_al_operador(
    outbox: Outbox, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="app.alerts"):
        lines = slow_turn()
    assert len(lines) == 1 and lines[0].startswith("ALERTA latencia")
    assert [r.getMessage() for r in caplog.records] == lines  # sigue en los logs
    assert len(outbox.sent) == 1
    email = outbox.sent[0]
    assert email.to == "operador@plataforma.example"
    assert "latencia" in email.subject.lower()
    assert lines[0] in email.body and str(CASE) in email.body and "15000" in email.body


def test_el_destino_se_puede_cambiar_y_sin_destino_no_se_envia(
    outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALERT_EMAIL_TO", "guardia@plataforma.example")
    slow_turn()
    assert [e.to for e in outbox.sent] == ["guardia@plataforma.example"]

    alerts.reset()
    monkeypatch.delenv("ALERT_EMAIL_TO")
    monkeypatch.delenv("PANEL_EMAIL")
    assert len(slow_turn()) == 1  # la alerta existe igual, en los logs
    assert len(outbox.sent) == 1


def test_no_inunda_la_casilla_con_la_misma_alerta(
    outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(alerts, "_now", lambda: clock[0])
    for _ in range(5):
        slow_turn()
    assert len(outbox.sent) == 1  # un email por tipo de alerta y empresa cada 15 minutos
    # Otro tipo de alerta, u otra empresa, sí avisa.
    alerts.check_turn(
        TENANT,
        CASE,
        {**SLOW, "total_ms": 100},
        case_cost=Decimal("0.60"),
        turn_cost=Decimal("0.20"),
    )
    alerts.check_turn(uuid.uuid4(), CASE, SLOW, case_cost=Decimal(0), turn_cost=Decimal(0))
    assert len(outbox.sent) == 3
    clock[0] += 15 * 60 + 1
    slow_turn()
    assert len(outbox.sent) == 4
    assert "4 alertas iguales" in outbox.sent[-1].body  # las que no se enviaron en el medio


def test_si_el_email_falla_la_alerta_no_rompe_nada(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    alerts.reset()
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setattr(alerts, "mailer_from_env", lambda: Outbox(failing=True))
    monkeypatch.setattr(alerts, "BACKGROUND", False)
    with caplog.at_level("WARNING", logger="app.alerts"):
        lines = slow_turn()
    assert len(lines) == 1
    messages = [r.getMessage() for r in caplog.records]
    assert lines[0] in messages and any("No se pudo enviar la alerta" in m for m in messages)


def test_sin_proveedor_de_email_la_alerta_queda_solo_en_los_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    alerts.reset()
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setattr(alerts, "mailer_from_env", lambda: ConsoleMailer())
    monkeypatch.setattr(alerts, "BACKGROUND", False)
    assert len(slow_turn()) == 1
    assert "Mailer de consola" not in capsys.readouterr().err  # no duplica la alerta en los logs


def test_la_alerta_de_errores_tambien_se_envia(outbox: Outbox) -> None:
    for moment in range(5):
        alerts.record_error(TENANT, "MailError", now=1000.0 + moment)
    assert len(outbox.sent) == 1 and "errores" in outbox.sent[0].subject.lower()
    assert "MailError" in outbox.sent[0].body
