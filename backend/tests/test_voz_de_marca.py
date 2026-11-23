"""Hito 12, tarea 4 — test de aceptación de la voz de marca (decisión 081).

En Configuración: tono, trato (tú, vos o usted), palabras a usar y a evitar, emojis sí o no y
firma. Se aplica en el prompt y la controla el supervisor.
"""

import json
import re
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack

from app.agent import voice
from app.audit.log import reconstruct_case
from app.main import app
from app.panel import auth, english
from app.panel.routes import PanelDeps, panel_deps

ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
ORDER_QUESTION = "¿Dónde está mi pedido 1001?"


def configure(stack: SimulatedStack, **values: object) -> voice.Voice:
    data = {"tono": "", "trato": "", "usar": "", "evitar": "", "emojis": True, "firma": ""}
    return voice.save(stack.conn, stack.seed.tenant_id, **{**data, **values})  # type: ignore[arg-type]


def agent_system(stack: SimulatedStack) -> str:
    return str(stack.agent_requests[-1]["body"]["system"])


def supervisor_requests(stack: SimulatedStack) -> list[dict]:
    return [r for r in stack.claude_requests if "supervisar" in r["tool_names"]]


@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn)


def test_sin_voz_configurada_el_prompt_no_cambia(stack: SimulatedStack) -> None:
    stack.send("lucia", "¿Abren los domingos?")
    assert "<voz_de_marca>" not in agent_system(stack)
    assert voice.load(stack.conn, stack.seed.tenant_id) == voice.Voice()


def test_se_aplica_en_el_prompt(stack: SimulatedStack) -> None:
    configure(
        stack,
        tono="cálido y cercano, sin vueltas",
        usar="genial, contá con nosotros",
        evitar="lamentablemente, estimado",
        emojis=False,
    )
    stack.send("lucia", "¿Abren los domingos?")
    system = agent_system(stack)
    assert "<voz_de_marca>" in system
    assert "cálido y cercano, sin vueltas" in system
    assert '"genial"' in system and '"contá con nosotros"' in system
    assert '"lamentablemente"' in system and '"estimado"' in system
    assert "Nunca uses emojis." in system


def test_el_trato_de_usted_cambia_el_tono_y_no_pasa_a_voseo(stack: SimulatedStack) -> None:
    configure(stack, trato="usted")
    stack.send("lucia", "¿Abren los domingos?")
    system = agent_system(stack)
    assert "usted" in system and "voseo" not in system


def test_la_firma_va_en_las_respuestas_del_agente_y_no_en_los_mensajes_fijos(
    stack: SimulatedStack,
) -> None:
    configure(stack, firma="— Sofi, de Acme")
    answer = stack.send("lucia", ORDER_QUESTION)
    assert answer.message.endswith("— Sofi, de Acme")
    fixed = stack.send("sofia", "¿Dónde está mi pedido 1006?")  # sin verificar: mensaje fijo
    assert fixed.verification_requested and "Sofi" not in fixed.message


def test_sin_emojis_se_sacan_antes_de_enviar(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, mode="emojis")
    with_emojis = stack.send("lucia", "¿Abren los domingos?")
    assert "😊" in with_emojis.message  # por defecto se permiten
    configure(stack, emojis=False)
    stack.close_open_cases()
    without = stack.send("lucia", "¿Abren los domingos?")
    assert "😊" not in without.message and without.message.strip()


def test_el_supervisor_frena_una_palabra_a_evitar_sin_llamar_al_modelo(
    stack: SimulatedStack,
) -> None:
    configure(stack, evitar="despachado")
    result = stack.send("lucia", ORDER_QUESTION)  # el simulado contesta "está despachado"
    assert result.action == "derivar"  # la reescritura vuelve a usarla: deriva
    assert supervisor_requests(stack) == []  # el control fijo no gasta modelo
    events = reconstruct_case(stack.conn, stack.seed.tenant_id, result.case_id)
    rejected = [e.payload for e in events if e.kind == "supervisor_rejected"]
    assert rejected and "despachado" in rejected[0]["motivo"]
    escalated = [e.payload for e in events if e.kind == "escalated"]
    assert escalated[0]["causa"]["tipo"] == "supervisor"


def test_el_supervisor_recibe_la_voz_de_marca(stack: SimulatedStack) -> None:
    configure(stack, tono="formal y preciso", trato="usted")
    stack.send("lucia", ORDER_QUESTION)
    [request] = supervisor_requests(stack)
    content = request["body"]["messages"][0]["content"]
    tone = re.search(r"<tono>(.*?)</tono>", content, re.S)
    assert tone is not None
    assert "formal y preciso" in tone.group(1) and "usted" in tone.group(1)


def test_un_trato_invalido_no_se_guarda(stack: SimulatedStack) -> None:
    with pytest.raises(voice.VoiceError):
        configure(stack, trato="che")


def test_las_palabras_se_limpian_y_se_limitan() -> None:
    found = voice.from_settings(
        {voice.SETTING: {"usar": " hola ,Hola, ,  chau  ", "evitar": ["x"] * 3, "emojis": False}}
    )
    assert found.usar == ("hola", "chau") and found.evitar == ("x",) and not found.emojis
    assert voice.avoided("Le escribo, Estimado cliente", voice.Voice(evitar=("estimado",)))
    assert not voice.avoided("estimadísimo", voice.Voice(evitar=("estimado",)))


# -- panel ---------------------------------------------------------------------------------------
@pytest.fixture
def panel(stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(stack.conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
    )
    client = TestClient(app)
    client.post("/panel/login", data={"email": ADMIN, "password": ADMIN_PASSWORD})
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        english.set_language("es")


def test_se_configura_en_configuracion(stack: SimulatedStack, panel: TestClient) -> None:
    page = panel.get("/panel/configuracion").text
    assert "Voz de marca" in page
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    panel.post(
        "/panel/configuracion/voz",
        data={
            "csrf": token.group(1),
            "tono": "cálido",
            "trato": "tu",
            "usar": "genial",
            "evitar": "lamentablemente",
            "firma": "Equipo Acme",
        },  # sin "emojis": la casilla destildada
    )
    saved = voice.load(stack.conn, stack.seed.tenant_id)
    assert saved == voice.Voice(
        "cálido", "tu", ("genial",), ("lamentablemente",), False, "Equipo Acme"
    )
    page = panel.get("/panel/configuracion").text
    assert 'value="cálido"' in page and json.dumps("Equipo Acme")[1:-1] in page
