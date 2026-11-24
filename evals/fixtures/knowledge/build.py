"""Arma los documentos de prueba de Knowledge (Hito 13, tarea 6; decisión 088). Son de Acme, la
empresa de demo, y ficticios. Se corre una vez y los archivos quedan en el repo:

- acme_manual_de_politicas.pdf: el HTML de al lado impreso con Chrome (Playwright), varias
  páginas con títulos, listas y tablas.
- acme_manual_de_atencion.docx: títulos (Heading 1 y 2), listas numeradas y con viñetas y una
  tabla, armado a mano con el XML de Word (sin dependencias).
- acme_catalogo.csv: 60 productos.

Uso: python evals/fixtures/knowledge/build.py   (el PDF necesita playwright y Chrome)
"""

import csv
import io
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# -- DOCX ----------------------------------------------------------------------------------------
MANUAL: list[tuple[str, object]] = [
    ("title", "Manual de atención al cliente de Acme Store"),
    ("p", "Guía interna del equipo de atención, revisión de noviembre de 2026."),
    ("h1", "Tono y saludo"),
    (
        "p",
        "Saludamos siempre con el nombre del cliente y tratamos de vos. Respondemos en "
        "mensajes cortos, sin tecnicismos y sin emojis en los reclamos.",
    ),
    ("h1", "Verificación de identidad"),
    (
        "p",
        "Antes de dar datos de un pedido o de hacer un cambio, verificamos la identidad del "
        "cliente con estos pasos:",
    ),
    ("ol", "Pedimos el número de pedido y el email de la compra."),
    ("ol", "Enviamos un código de 6 dígitos al email registrado."),
    ("ol", "El código vence a los 10 minutos y se puede pedir 3 veces por hora."),
    ("ol", "Si el cliente no tiene acceso a su email, derivamos a un supervisor."),
    ("h1", "Escalamiento de casos"),
    ("p", "Cada tipo de caso tiene un plazo de respuesta y un responsable:"),
    (
        "table",
        [
            ["Tipo de caso", "Plazo de respuesta", "Responsable"],
            ["Pedido demorado", "24 horas hábiles", "Equipo de logística"],
            ["Producto con falla", "48 horas hábiles", "Equipo de calidad"],
            ["Reembolso de más de $ 150.000", "72 horas hábiles", "Supervisor de atención"],
            ["Reclamo en Defensa del Consumidor", "Mismo día", "Área legal"],
        ],
    ),
    ("h1", "Reclamos por envíos"),
    ("h2", "Paquete marcado como entregado y no recibido"),
    (
        "p",
        "Pedimos al cliente que revise con vecinos y portería durante 24 horas. Si no aparece, "
        "abrimos un reclamo con el correo, que tiene 10 días hábiles para responder, y le "
        "ofrecemos un reenvío sin costo o el reembolso.",
    ),
    ("h2", "Paquete dañado"),
    (
        "p",
        "El cliente tiene que mandar fotos del paquete y del producto dentro de las 48 horas "
        "de recibido. Con las fotos, el cambio o el reembolso se aprueba sin devolver el "
        "producto si vale menos de $ 25.000.",
    ),
    ("h1", "Compensaciones"),
    ("p", "Podemos ofrecer estas compensaciones sin pedir autorización:"),
    ("ul", "Un cupón del 10% para la próxima compra por una demora de más de 5 días hábiles."),
    ("ul", "El reintegro del costo del envío si el pedido llegó incompleto."),
    (
        "ul",
        "Un cupón de $ 5.000 si el cliente tuvo que contactarnos más de 3 veces por el mismo tema.",
    ),
    ("p", "Cualquier otra compensación la autoriza un supervisor."),
    ("h1", "Datos personales"),
    (
        "p",
        "Nunca pedimos la clave de la tarjeta ni el código de seguridad. Los datos del cliente "
        "se borran a pedido dentro de los 10 días hábiles, salvo las facturas, que se guardan "
        "10 años por obligación fiscal.",
    ),
]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(text: str, style: str = "", num: int | None = None) -> str:
    props = f'<w:pStyle w:val="{style}"/>' if style else ""
    if num is not None:
        props += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/></w:numPr>'
    return f"<w:p><w:pPr>{props}</w:pPr>{_run(text)}</w:p>"


def _table(rows: list[list[str]]) -> str:
    body = "".join(
        "<w:tr>" + "".join(f"<w:tc>{_para(cell)}</w:tc>" for cell in row) + "</w:tr>"
        for row in rows
    )
    return f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>{body}</w:tbl>'


def build_docx(path: Path) -> None:
    parts = []
    for kind, value in MANUAL:
        if kind == "title":
            parts.append(_para(str(value), "Title"))
        elif kind == "h1":
            parts.append(_para(str(value), "Heading1"))
        elif kind == "h2":
            parts.append(_para(str(value), "Heading2"))
        elif kind == "ol":
            parts.append(_para(str(value), "ListParagraph", num=1))
        elif kind == "ul":
            parts.append(_para(str(value), "ListParagraph", num=2))
        elif kind == "table":
            parts.append(_table(value))  # type: ignore[arg-type]
        else:
            parts.append(_para(str(value)))
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}">'
        f"<w:body>{''.join(parts)}</w:body></w:document>"
    )
    styles = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
        + "".join(
            f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/></w:style>'
            for sid, name in (
                ("Title", "Title"),
                ("Heading1", "heading 1"),
                ("Heading2", "heading 2"),
                ("ListParagraph", "List Paragraph"),
            )
        )
        + "</w:styles>"
    )
    numbering = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="{W}">'
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/>'
        '</w:lvl></w:abstractNum><w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0">'
        '<w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>'
    )
    rels_doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/numbering" Target="numbering.xml"/></Relationships>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.numbering+xml"/></Types>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", types)
        bundle.writestr("_rels/.rels", rels)
        bundle.writestr("word/_rels/document.xml.rels", rels_doc)
        bundle.writestr("word/document.xml", document)
        bundle.writestr("word/styles.xml", styles)
        bundle.writestr("word/numbering.xml", numbering)
    path.write_bytes(buffer.getvalue())


# -- CSV -----------------------------------------------------------------------------------------
KINDS = [
    ("Campera", "Abrigos", 45900),
    ("Buzo", "Abrigos", 28900),
    ("Remera", "Remeras", 12900),
    ("Pantalón", "Pantalones", 32900),
    ("Zapatilla", "Calzado", 64900),
    ("Gorra", "Accesorios", 9900),
]
NAMES = ["Norte", "Delta", "Patagonia", "Urbana", "Andes", "Litoral", "Pampa", "Austral",
         "Río", "Cumbre"]  # fmt: skip
COLORS = ["negro", "azul", "verde", "gris", "rojo", "blanco"]


def build_csv(path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["sku", "nombre", "categoria", "precio", "talles", "colores", "stock"])
    for i, (kind, category, base) in enumerate(KINDS):
        for j, name in enumerate(NAMES):
            sizes = "S a XXL"
            if category == "Calzado":
                sizes = "35 al 44"
            elif kind == "Gorra":
                sizes = "Único"
            colors = ", ".join(COLORS[(i + j) % 6 : (i + j) % 6 + 2] or COLORS[:2])
            writer.writerow(
                [
                    f"AC-{i + 1}{j:02d}",
                    f"{kind} {name}",
                    category,
                    f"$ {base + 1000 * j:,}".replace(",", "."),
                    sizes,
                    colors,
                    (7 * i + 3 * j) % 25,
                ]
            )
    path.write_text(out.getvalue(), encoding="utf-8")


# -- PDF -----------------------------------------------------------------------------------------
def build_pdf(source: Path, path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME)
        page = browser.new_page()
        page.goto(source.as_uri())
        page.pdf(path=str(path), format="A4")
        browser.close()


def main() -> int:
    build_docx(HERE / "acme_manual_de_atencion.docx")
    build_csv(HERE / "acme_catalogo.csv")
    if "--sin-pdf" not in sys.argv:
        build_pdf(HERE / "acme_manual_de_politicas.html", HERE / "acme_manual_de_politicas.pdf")
    print("listo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
