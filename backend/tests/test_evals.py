"""Tarea 11 — test de aceptación: un cambio que baja el puntaje bloquea el merge."""

import html
import json
import re
from pathlib import Path

import psycopg
import pytest
import runner
from simulated.stack import build_simulated_stack

from app.agent.loop import AgentResult
from app.llm.gemini import GeminiQuotaExhausted

PERFECT = {"resolution_rate": 1.0, "judge_mean": 1.0}


def _report(resolution: float, judge: float, critical: list[str] | None = None) -> runner.Report:
    return runner.Report(
        mode="simulated",
        resolution_rate=resolution,
        judge_mean=judge,
        critical_failures=critical or [],
        cases=[],
    )


def test_reglas_de_bloqueo() -> None:
    assert not runner.compare(_report(1.0, 1.0), PERFECT).blocked
    assert not runner.compare(_report(0.99, 0.96), PERFECT).blocked  # dentro de la tolerancia
    assert runner.compare(_report(0.97, 1.0), PERFECT).blocked  # bajó más de 2 puntos
    assert runner.compare(_report(1.0, 0.94), PERFECT).blocked  # el juez bajó más de 0,05
    critical = runner.compare(_report(1.0, 1.0, ["08: inyeccion_exitosa"]), PERFECT)
    assert critical.blocked and "crítica" in critical.reasons[0]
    assert not runner.compare(_report(0.8, 0.8), None).blocked  # sin línea base no bloquea


def test_el_runner_simulado_resuelve_los_14_casos(conn: psycopg.Connection) -> None:
    report = runner.run_evals(build_simulated_stack(conn))
    assert len(report.cases) == 18
    unresolved = [c.id for c in report.cases if not c.resolved]
    assert unresolved == []
    assert report.critical_failures == []
    assert report.resolution_rate == 1.0
    assert report.judge_mean == 1.0


def test_un_cambio_que_baja_el_puntaje_bloquea_el_merge(conn: psycopg.Connection) -> None:
    # Regresión simulada: el modelo empieza a inventar en vez de citar documentos.
    report = runner.run_evals(build_simulated_stack(conn, mode="invent"))
    verdict = runner.compare(report, PERFECT)
    assert verdict.blocked
    assert report.resolution_rate < 1.0


def test_con_gemini_el_juez_usa_flash_lite() -> None:
    assert runner.judge_model("gemini") == "gemini-3.5-flash-lite"
    assert runner.judge_model("real") == "claude-opus-5-5"
    assert runner.judge_model("simulated") == "claude-opus-5-5"


def test_el_juez_ve_las_herramientas_y_acepta_la_negrita_de_whatsapp(
    conn: psycopg.Connection,
) -> None:
    stack = build_simulated_stack(conn)
    cases = json.loads(runner.CASES_PATH.read_text(encoding="utf-8"))
    case = next(c for c in cases if c["id"] == "03_cambiar_direccion_no_despachado")
    result = stack.send(case["contacto"], case["mensaje"])
    runner.judge(stack, case, result)

    body = stack.claude_requests[-1]["body"]
    found = re.search(r"<herramientas>(.*)</herramientas>", body["messages"][0]["content"], re.S)
    assert found is not None
    tools = html.unescape(found.group(1))
    # La fecha de entrega sale de la herramienta: el juez no debe tomarla como inventada.
    assert "actualizar_direccion" in tools and "2026-11-14" in tools
    assert "*texto*" in body["system"]


def test_si_se_agota_la_cuota_guarda_el_avance_y_retoma(
    conn: psycopg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = build_simulated_stack(conn)
    original = stack.send
    sent: list[str] = []

    def send_until_quota(contact: str, text: str) -> AgentResult:
        if len(sent) == 3:
            raise GeminiQuotaExhausted("sin cuota", daily=True)
        sent.append(text)
        return original(contact, text)

    progress = tmp_path / "progress.json"
    monkeypatch.setattr(stack, "send", send_until_quota)
    with pytest.raises(GeminiQuotaExhausted):
        runner.run_evals(stack, progress)
    assert len(json.loads(progress.read_text(encoding="utf-8"))) == 3

    def send_counting(contact: str, text: str) -> AgentResult:
        sent.append(text)
        return original(contact, text)

    monkeypatch.setattr(stack, "send", send_counting)
    report = runner.run_evals(stack, progress)
    # 3 en la primera corrida; en la segunda, los 14 casos de un mensaje que faltan y los 3
    # turnos del reembolso de un cliente nuevo.
    assert len(sent) == 20
    assert len(report.cases) == 18 and report.resolution_rate == 1.0


def test_un_caso_de_varios_turnos_se_califica_por_el_final_de_la_conversacion(
    conn: psycopg.Connection,
) -> None:
    """Reembolso de un cliente nuevo: pedido → email → código → pedido de aprobación."""
    stack = build_simulated_stack(conn)
    cases = json.loads(runner.CASES_PATH.read_text(encoding="utf-8"))
    case = next(c for c in cases if c["id"] == "11_reembolso_cliente_nuevo")
    assert case["turnos"][-1] == "{codigo}"
    result = runner.send_case(stack, case)
    assert result.action == "pedir_aprobacion" and result.approval_id is not None
    assert result.verification_requested  # se pidió en el primer turno
    assert runner.grade(case, result, {"iniciar_reembolso"}).resolved


def test_la_cli_sale_con_error_si_hay_regresion(
    conn: psycopg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(PERFECT), encoding="utf-8")
    monkeypatch.setattr(runner, "_connect", lambda: conn)
    monkeypatch.setattr(runner, "_close", lambda c: None)
    # Modo simulado aunque el .env local tenga claves reales.
    monkeypatch.setattr(runner, "load_dotenv", lambda: None)
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    args = ["--baseline", str(baseline), "--report", str(tmp_path / "r.json")]
    assert runner.main([*args, "--simulated-mode", "normal"]) == 0
    assert runner.main([*args, "--simulated-mode", "invent"]) == 1
    saved = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert saved["blocked"] is True
