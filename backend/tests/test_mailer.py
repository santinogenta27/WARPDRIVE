"""Emails transaccionales detrás de la interfaz `Mailer`.

- Sin proveedor configurado, `ConsoleMailer` escribe el código en los logs del servidor (Render),
  con su propio handler y flush en cada línea, sin depender de cómo configure uvicorn el logging.
- Con `RESEND_API_KEY` y `MAIL_FROM`, `ResendMailer` envía por la API HTTP de Resend. Acá el
  envío es simulado: ningún test sale a la red.
"""

import json
import logging

import httpx
import pytest

from app.mailer import ConsoleMailer, Email, MailError, ResendMailer, mailer_from_env

# Una dirección que no es de prueba: las @example.com no se envían (ver más abajo).
EMAIL = Email(to="martin@correo-del-cliente.com", subject="Tu código", body="Tu código es 123456.")


def test_el_codigo_se_escribe_en_los_logs_del_servidor(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()  # como en Render: uvicorn no le pone handler al logger raíz
    try:
        ConsoleMailer().send(EMAIL)
    finally:
        root.handlers[:] = saved
    err = capsys.readouterr().err
    assert "[Mailer de consola, no se envió]" in err and "123456" in err
    assert err.count("123456") == 1  # una sola línea, sin duplicados por propagación


def _resend(handler: httpx.MockTransport) -> ResendMailer:
    return ResendMailer(
        api_key="re_test_123",
        sender="Acme Store <codigos@enterprisedataai.com>",
        http=httpx.Client(transport=handler),
    )


def test_resend_envia_el_email_por_la_api() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "abc"})

    _resend(httpx.MockTransport(handler)).send(EMAIL)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_test_123"
    assert json.loads(request.content) == {
        "from": "Acme Store <codigos@enterprisedataai.com>",
        "to": ["martin@correo-del-cliente.com"],
        "subject": "Tu código",
        "text": "Tu código es 123456.",
    }


def test_resend_avisa_si_el_proveedor_rechaza_el_envio(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"name": "validation_error", "message": "dominio"})

    with pytest.raises(MailError) as error:
        _resend(httpx.MockTransport(handler)).send(EMAIL)
    # El error dice qué pasó, sin el destinatario, el código ni la clave.
    text = str(error.value) + capsys.readouterr().err
    assert "403" in str(error.value) and "validation_error" in str(error.value)
    assert "martin@" not in text and "123456" not in text and "re_test" not in text


def test_resend_avisa_si_no_hay_red() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    with pytest.raises(MailError):
        _resend(httpx.MockTransport(handler)).send(EMAIL)


@pytest.mark.parametrize(
    "address", ["lucia@example.com", "demo@acme.example", "a@sub.example.org", "x@algo.test"]
)
def test_resend_no_envia_a_dominios_de_prueba(
    address: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Los clientes de la demo de Acme tienen emails @example.com, que no existen: enviarles
    rebota y daña la reputación del dominio. Su código sigue saliendo en los logs."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no tiene que salir a la red")

    _resend(httpx.MockTransport(handler)).send(Email(to=address, subject="s", body="654321"))
    err = capsys.readouterr().err
    assert "[Mailer de consola, no se envió]" in err and "654321" in err


def test_sin_variables_sigue_el_mailer_de_consola() -> None:
    assert isinstance(mailer_from_env({}), ConsoleMailer)
    # Hacen falta las dos: con una sola no se puede enviar.
    assert isinstance(mailer_from_env({"RESEND_API_KEY": "re_test_123"}), ConsoleMailer)
    assert isinstance(mailer_from_env({"MAIL_FROM": "a@enterprisedataai.com"}), ConsoleMailer)
    assert isinstance(mailer_from_env({"RESEND_API_KEY": " ", "MAIL_FROM": " "}), ConsoleMailer)


def test_con_las_dos_variables_se_envia_por_resend() -> None:
    mailer = mailer_from_env(
        {"RESEND_API_KEY": "re_test_123", "MAIL_FROM": "Acme <codigos@enterprisedataai.com>"}
    )
    assert isinstance(mailer, ResendMailer)
