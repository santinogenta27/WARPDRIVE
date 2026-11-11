"""El código de verificación de la demo se lee en los logs del servidor (Render): ConsoleMailer
lo escribe con logging, con su propio handler y flush en cada línea, sin depender de cómo
configure uvicorn el logging."""

import logging

import pytest

from app.mailer import ConsoleMailer, Email


def test_el_codigo_se_escribe_en_los_logs_del_servidor(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()  # como en Render: uvicorn no le pone handler al logger raíz
    try:
        ConsoleMailer().send(
            Email(to="martin@example.com", subject="Tu código", body="Tu código es 123456.")
        )
    finally:
        root.handlers[:] = saved
    err = capsys.readouterr().err
    assert "[Mailer de consola, no se envió]" in err and "123456" in err
    assert err.count("123456") == 1  # una sola línea, sin duplicados por propagación
