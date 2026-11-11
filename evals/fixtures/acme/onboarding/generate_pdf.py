"""Genera los PDF de prueba de Acme Store (empresa ficticia), sin dependencias:

- `garantia.pdf`: una política de dos páginas, con texto.
- `garantia_escaneada.pdf`: una página que es solo una imagen, como un documento escaneado.

Uso: python evals/fixtures/acme/onboarding/generate_pdf.py
Es determinista: correrlo de nuevo deja los mismos archivos.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent

PAGES = [
    [
        "Garantía de productos",
        "",
        "Todos los productos de Acme Store tienen 6 meses de garantía por fallas",
        "de fabricación, contados desde la fecha de entrega.",
        "",
        "La garantía no cubre el desgaste por el uso ni los daños por un lavado",
        "distinto del que indica la etiqueta.",
        "",
        "Para usarla, escribinos a ayuda@acmestore.example con el número de pedido",
        "y una foto de la falla. Respondemos en 2 días hábiles.",
    ],
    [
        "Productos en liquidación",
        "",
        "Los productos comprados en liquidación tienen 3 meses de garantía.",
        "",
        "Si confirmamos la falla, cambiamos el producto o devolvemos el dinero",
        "en hasta 10 días hábiles, por el mismo medio de pago.",
    ],
]


def _text_stream(lines: list[str]) -> bytes:
    parts = [b"BT /F1 12 Tf 72 740 Td 18 TL"]
    for line in lines:
        raw = line.encode("cp1252")
        for char in (b"\\", b"(", b")"):
            raw = raw.replace(char, b"\\" + char)
        parts.append(b"(" + raw + b") Tj T*")
    parts.append(b"ET")
    return b"\n".join(parts)


def _stream(dictionary: bytes, data: bytes) -> bytes:
    return (
        b"<< "
        + dictionary
        + b" /Length "
        + str(len(data)).encode()
        + b" >>\nstream\n"
        + data
        + b"\nendstream"
    )


def _assemble(objects: list[bytes]) -> bytes:
    """Arma el archivo: los objetos numerados desde 1 (el 1 es el catálogo) y la tabla xref."""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref).encode() + b"\n%%EOF\n"
    return bytes(out)


def _document(contents: list[bytes], resources: bytes, extra: list[bytes]) -> bytes:
    """Objetos: 1 catálogo, 2 páginas, después `extra` (fuente o imagen) y, por cada página,
    su objeto y su contenido."""
    first_page = 3 + len(extra)
    kids = b" ".join(f"{first_page + 2 * i} 0 R".encode() for i in range(len(contents)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(contents)).encode() + b" >>",
        *extra,
    ]
    for index, content in enumerate(contents):
        number = first_page + 2 * index
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << "
            + resources
            + b" >> /Contents "
            + f"{number + 1} 0 R".encode()
            + b" >>"
        )
        objects.append(_stream(b"", content))
    return _assemble(objects)


def text_pdf(pages: list[list[str]] = PAGES) -> bytes:
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    return _document([_text_stream(p) for p in pages], b"/Font << /F1 3 0 R >>", [font])


def scanned_pdf() -> bytes:
    # Una imagen gris de 2 x 2 píxeles estirada a toda la página: no hay texto para extraer.
    image = _stream(
        b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray"
        b" /BitsPerComponent 8",
        b"\xd0\xe0\xe0\xd0",
    )
    return _document([b"q 595 0 0 842 0 0 cm /Im1 Do Q"], b"/XObject << /Im1 3 0 R >>", [image])


if __name__ == "__main__":
    (HERE / "garantia.pdf").write_bytes(text_pdf())
    (HERE / "garantia_escaneada.pdf").write_bytes(scanned_pdf())
    print("Listo: garantia.pdf y garantia_escaneada.pdf")
