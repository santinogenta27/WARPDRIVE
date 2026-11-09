"""Tarea 11 — test de aceptación: un cambio que baja el puntaje bloquea el merge."""

import json
from pathlib import Path

import psycopg
import pytest
import runner
from simulated.stack import build_simulated_stack

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


def test_el_runner_simulado_resuelve_los_10_casos(conn: psycopg.Connection) -> None:
    report = runner.run_evals(build_simulated_stack(conn))
    assert len(report.cases) == 10
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


def test_la_cli_sale_con_error_si_hay_regresion(
    conn: psycopg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(PERFECT), encoding="utf-8")
    monkeypatch.setattr(runner, "_connect", lambda: conn)
    monkeypatch.setattr(runner, "_close", lambda c: None)
    # Modo simulado aunque el .env local tenga claves reales.
    monkeypatch.setattr(runner, "load_dotenv", lambda: None)
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    args = ["--baseline", str(baseline), "--report", str(tmp_path / "r.json")]
    assert runner.main([*args, "--simulated-mode", "normal"]) == 0
    assert runner.main([*args, "--simulated-mode", "invent"]) == 1
    saved = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert saved["blocked"] is True
