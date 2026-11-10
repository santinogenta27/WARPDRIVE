"""Flujos de la demo con el modelo real de Groq (el del chat en Render), no el simulado.

Gastan cuota del nivel gratuito, así que corren solo a pedido:

    RUN_GROQ_TESTS=1 python -m pytest backend/tests/test_groq_real.py
    RUN_GROQ_TESTS=1 GROQ_REPEAT=5 ...   # repite el flujo para medir si es estable

Sin caché del modelo: cada corrida le pregunta de verdad a Groq. Los embeddings sí usan la caché
de los evals (los documentos no cambian).
"""

import json
import os
from collections.abc import Iterator

import psycopg
import pytest
import runner
from simulated.stack import SimulatedStack, build_real_stack

from app.config import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_GROQ_TESTS") != "1" or not os.environ.get("GROQ_API_KEY"),
    reason="Gasta cuota de Groq: correr con RUN_GROQ_TESTS=1 y GROQ_API_KEY",
)
REPEAT = int(os.environ.get("GROQ_REPEAT") or 1)
TUTEO = ("indícanos", "dime ", "puedes", "quieres", "tienes", "necesitas")


@pytest.fixture
def groq(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimulatedStack]:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    # Los mismos modelos que el chat en Render (render.yaml).
    monkeypatch.setenv("GROQ_AGENT_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_CLASSIFIER_MODEL", "openai/gpt-oss-20b")
    yield build_real_stack(conn, runner.CACHE_DIR, cache_models=False)


def case(case_id: str) -> dict[str, object]:
    cases = json.loads(runner.CASES_PATH.read_text(encoding="utf-8"))
    found: dict[str, object] = next(c for c in cases if c["id"] == case_id)
    return found


@pytest.mark.parametrize("attempt", range(REPEAT))
def test_reembolso_de_un_cliente_nuevo_verifica_por_codigo_y_pide_aprobacion(
    groq: SimulatedStack, attempt: int
) -> None:
    refund = case("11_reembolso_cliente_nuevo")
    result = runner.send_case(groq, refund)
    assert result.verification_requested
    assert result.action == "pedir_aprobacion", result.message
    assert result.approval_id is not None
    assert not any(word in result.message.lower() for word in TUTEO), result.message
