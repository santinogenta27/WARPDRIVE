"""Arma los documentos de la tienda inventada del QA de punta a punta (Hito QA 2, etapa 2).

Brightside Outdoor Co. es ficticia: no es un cliente real. Se corre una vez y los archivos quedan
en el repo:

- brightside_policies.pdf: `policies.html` impreso con Chrome (Playwright).
- brightside_shipping_guide.docx: envíos, garantía y cuidado de los productos, con el mismo armado
  a mano del XML de Word que los documentos de Knowledge (sin dependencias).
- brightside_faq.csv: preguntas frecuentes, una por fila.

Uso: python evals/fixtures/brightside/build_docs.py   (el PDF necesita playwright y Chrome)
"""

import csv
import importlib.util
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "knowledge_build", HERE.parent / "knowledge" / "build.py"
)
assert _spec is not None and _spec.loader is not None
knowledge_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(knowledge_build)

GUIDE: list[tuple[str, object]] = [
    ("title", "Brightside Outdoor Co. shipping and warranty guide"),
    ("p", "Customer guide of a fictional store used only for testing. Revised November 2026."),
    ("h1", "Shipping"),
    ("p", "We ship to every state in the continental US."),
    ("ul", "Standard shipping takes 3 to 6 business days and is free on orders over $75."),
    ("ul", "Express shipping takes 1 to 2 business days and costs $15."),
    ("ul", "Orders placed before 1 p.m. on a business day leave our warehouse the same day."),
    ("h1", "Warranty"),
    ("p", "Every Brightside product has a warranty against manufacturing defects:"),
    (
        "table",
        [
            ["Product", "Warranty"],
            ["Tents", "2 years"],
            ["Jackets", "1 year"],
            ["Backpacks", "18 months"],
        ],
    ),
    ("p", "Damage from normal wear, accidents or misuse isn't covered."),
    ("h1", "Product care"),
    ("ol", "Wash jackets in cold water with a technical wash, never with fabric softener."),
    ("ol", "Dry tents completely before storing them to prevent mildew."),
]

FAQ = [
    ("Do you ship to Canada?", "Not yet. We only ship within the continental US."),
    ("Can I change my shipping address?", "Yes, while the order hasn't shipped. Ask the chat."),
    ("Do you have a physical store?", "No. Brightside Outdoor Co. sells online only."),
    ("Do you offer gift wrapping?", "Yes, for $5 per order. Choose it at checkout."),
    ("Can I pay in installments?", "Yes, with Shop Pay in 4 interest-free payments."),
]


def build_docx(path: Path) -> None:
    setattr(knowledge_build, "MANUAL", GUIDE)  # noqa: B010 (el armado lee esta lista)
    knowledge_build.build_docx(path)


def build_csv(path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["question", "answer"])
    writer.writerows(FAQ)
    path.write_text(out.getvalue(), encoding="utf-8")


def main() -> int:
    build_docx(HERE / "brightside_shipping_guide.docx")
    build_csv(HERE / "brightside_faq.csv")
    if "--sin-pdf" not in sys.argv:
        knowledge_build.build_pdf(HERE / "policies.html", HERE / "brightside_policies.pdf")
    print("listo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
