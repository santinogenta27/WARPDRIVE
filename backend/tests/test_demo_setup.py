"""La demo de Acme toma de los fixtures las instrucciones de los tipos de caso y las
herramientas: `make demo-setup` actualiza una base que ya tenía la demo cargada."""

import psycopg
import yaml
from simulated.stack import build_simulated_stack

from app.db.connection import tenant_scope
from app.demo.acme import ACME_DIR, sync_demo_config


def test_la_carga_de_la_demo_actualiza_tipos_de_caso_y_herramientas(
    conn: psycopg.Connection,
) -> None:
    stack = build_simulated_stack(conn)
    tenant = stack.seed.tenant_id
    with tenant_scope(conn, tenant):
        conn.execute("update case_types set instructions = 'vieja' where name = 'reembolso'")
        conn.execute("update tools set manifest = '{}' where name = 'iniciar_reembolso'")

    sync_demo_config(conn, tenant)

    with tenant_scope(conn, tenant):
        instructions = conn.execute(
            "select instructions from case_types where name = 'reembolso'"
        ).fetchone()
        manifest = conn.execute(
            "select manifest from tools where name = 'iniciar_reembolso'"
        ).fetchone()
    assert instructions is not None and manifest is not None
    expected = (ACME_DIR / "case_types" / "reembolso.yaml").read_text(encoding="utf-8")
    assert instructions[0] == expected and manifest[0]["risk"] == "high"


def test_el_reembolso_se_inicia_por_el_total_sin_preguntar_monto_ni_motivo() -> None:
    """Con el modelo real, preguntar el monto o el motivo cortaba el flujo de la demo."""
    refund = yaml.safe_load((ACME_DIR / "case_types" / "reembolso.yaml").read_text("utf-8"))
    assert refund["datos_necesarios"] == ["order_id"]
    rules = " ".join(refund["condiciones"])
    assert "total del pedido" in rules and "No preguntes el motivo" in rules
