"""Hito 8, tarea 7 — test de aceptación del guion del video en inglés (`docs/demo-en.md`).

El guion se probó de punta a punta en Chrome con el modelo real (capturas en
`docs/img/demo-en/`). Este test lo mantiene en sincronía con el código:

- los mensajes que se escriben en el video son los del caso de evals del guion (nw21), que el CI
  corre con el modelo simulado en cada cambio;
- las capturas que cita existen;
- nombra la página, el comando y el usuario de la demo.
"""

import json
import re

from app.config import REPO_ROOT
from app.demo import northwind

DOC = REPO_ROOT / "docs" / "demo-en.md"


def _script_case(name: str = "nw21_demo_script") -> dict[str, object]:
    path = REPO_ROOT / "evals" / "fixtures" / "northwind" / "scripted_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    found: dict[str, object] = next(c for c in cases if c["id"] == name)
    return found


def test_los_mensajes_del_video_son_los_del_eval_del_guion() -> None:
    text = DOC.read_text(encoding="utf-8")
    typed = re.findall(r"\*\*`([^`]+)`\*\*", text)  # lo que se escribe, en negrita y código
    turns = [t for t in _script_case()["turnos"] if t not in ("{codigo}",)]  # type: ignore[attr-defined]
    # El primero se manda con la sugerencia del chat y el email va en el paso de verificación.
    assert turns[0] in text and northwind.company()["widget"]["suggestions"][0] == turns[0]
    assert "`emily@example.com`" in text and turns[1] == "emily@example.com"
    # El paso de venta (decisión 065) es el caso nw29: el primero, con la segunda sugerencia.
    sale = _script_case("nw29_sale_checkout_link_and_one_offer")["turnos"]
    assert f"*{sale[0]}*" in text and northwind.company()["widget"]["suggestions"][1] == sale[0]  # type: ignore[index]
    assert typed == turns[2:] + sale[1:]  # type: ignore[operator,index]


def test_las_capturas_existen() -> None:
    text = DOC.read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    assert len(images) >= 7
    for image in images:
        assert (DOC.parent / image).is_file(), image


def test_nombra_la_pagina_el_comando_y_el_usuario() -> None:
    text = DOC.read_text(encoding="utf-8")
    slug = northwind.company()["chat_slug"]
    for needed in (f"/demo/{slug}", "make demo-setup", northwind.company()["panel_user"]):
        assert needed in text, needed
