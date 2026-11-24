"""Extracción con estructura (Hito 13, tarea 6; decisión 088).

Cada formato se convierte en un texto tipo Markdown que conserva lo que ayuda a responder:

- **títulos** (`#`, `##`, `###`): de los estilos de Word, de la numeración y el largo de las
  líneas de un PDF, y de `<h1>`…`<h6>` de una página;
- **listas**, un ítem por renglón (`- …`);
- **tablas**, una fila por renglón con el nombre de cada columna
  (`Zona: CABA · Costo del envío: $ 3.900 · …`), así una fila se entiende sola;
- **párrafos** separados por una línea en blanco.

`rag.indexer.chunk_text` usa esa estructura para no cortar una política a la mitad.
"""

import csv
import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
TERMINAL = (".", ":", "!", "?", ";", "…", ")")
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
PAGE_MARK = re.compile(r"^(?:p[áa]gina|page)?\s*\d+(?:\s*(?:de|of|/)\s*\d+)?$", re.IGNORECASE)
GAP = re.compile(r"\s{3,}")


def table_rows(rows: list[list[str]]) -> list[str]:
    """Filas de una tabla con el nombre de cada columna. La primera fila es el encabezado; si
    una celda no tiene encabezado, va sola."""
    rows = [[cell.strip() for cell in row] for row in rows if any(c.strip() for c in row)]
    if not rows:
        return []
    if len(rows) == 1:
        return [" · ".join(c for c in rows[0] if c)]
    header = rows[0]
    lines = []
    for row in rows[1:]:
        parts = [
            f"{header[i]}: {cell}" if i < len(header) and header[i] else cell
            for i, cell in enumerate(row)
            if cell
        ]
        lines.append(" · ".join(parts))
    return lines


# -- Word ------------------------------------------------------------------------------------------
def _style_level(style: str, names: dict[str, str]) -> int:
    name = names.get(style, style).lower().replace(" ", "")
    if name == "title":
        return 1
    found = re.match(r"heading(\d)", name)
    return min(int(found.group(1)) + 1, 6) if found else 0


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        root = ElementTree.fromstring(bundle.read("word/document.xml"))
        names: dict[str, str] = {}
        if "word/styles.xml" in bundle.namelist():
            styles = ElementTree.fromstring(bundle.read("word/styles.xml"))
            for node in styles.iter(f"{W}style"):
                name = node.find(f"{W}name")
                if name is not None:
                    names[str(node.get(f"{W}styleId"))] = str(name.get(f"{W}val"))
    body = root.find(f"{W}body")
    blocks: list[str] = []
    items: list[str] = []
    counter = 0

    def close_list() -> None:
        nonlocal counter
        if items:
            blocks.append("\n".join(items))
            items.clear()
        counter = 0

    for element in list(body) if body is not None else []:
        if element.tag == f"{W}tbl":
            close_list()
            rows = [
                ["".join(t.text or "" for t in cell.iter(f"{W}t")) for cell in row.iter(f"{W}tc")]
                for row in element.iter(f"{W}tr")
            ]
            lines = table_rows(rows)
            if lines:
                blocks.append("\n".join(lines))
            continue
        if element.tag != f"{W}p":
            continue
        text = "".join(t.text or "" for t in element.iter(f"{W}t")).strip()
        if not text:
            continue
        props = element.find(f"{W}pPr")
        style = ""
        numbered = False
        if props is not None:
            found = props.find(f"{W}pStyle")
            style = str(found.get(f"{W}val")) if found is not None else ""
            numbered = props.find(f"{W}numPr") is not None
        level = _style_level(style, names)
        if level:
            close_list()
            blocks.append(f"{'#' * level} {text}")
        elif numbered or "list" in names.get(style, style).lower():
            num = props.find(f"{W}numPr") if props is not None else None
            num_id = num.find(f"{W}numId") if num is not None else None
            ordered = num_id is not None and _ordered(bundle_numbering(data), num_id.get(f"{W}val"))
            counter += 1
            items.append(f"{counter}. {text}" if ordered else f"- {text}")
        else:
            close_list()
            blocks.append(text)
    close_list()
    return "\n\n".join(blocks)


_NUMBERING: dict[int, dict[str, bool]] = {}


def bundle_numbering(data: bytes) -> dict[str, bool]:
    """¿Cada lista de Word es numerada (decimal) o con viñetas? Se lee una vez por archivo."""
    key = hash(data)
    if key in _NUMBERING:
        return _NUMBERING[key]
    ordered: dict[str, bool] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        if "word/numbering.xml" in bundle.namelist():
            root = ElementTree.fromstring(bundle.read("word/numbering.xml"))
            formats = {}
            for abstract in root.iter(f"{W}abstractNum"):
                fmt = abstract.find(f"{W}lvl/{W}numFmt")
                formats[abstract.get(f"{W}abstractNumId")] = fmt is not None and fmt.get(
                    f"{W}val"
                ) not in ("bullet", "none")
            for num in root.iter(f"{W}num"):
                ref = num.find(f"{W}abstractNumId")
                if ref is not None:
                    ordered[str(num.get(f"{W}numId"))] = formats.get(ref.get(f"{W}val"), False)
    _NUMBERING.clear()  # solo el último archivo: no crece
    _NUMBERING[key] = ordered
    return ordered


def _ordered(numbering: dict[str, bool], num_id: str | None) -> bool:
    return bool(num_id and numbering.get(num_id))


# -- PDF -------------------------------------------------------------------------------------------
def _cells(line: str) -> list[str]:
    return [c for c in GAP.split(line.strip()) if c]


def _is_heading(line: str, nxt: str | None) -> int:
    """El nivel de título de una línea suelta de un PDF (0 si no es un título)."""
    text = line.strip()
    if len(text) > 90 or text.endswith(TERMINAL) or len(_cells(line)) > 1:
        return 0
    numbered = NUMBERED.match(text)
    if numbered:
        return 2 + numbered.group(1).count(".")
    words = text.split()
    if len(words) <= 8 and text[:1].isupper() and nxt is not None:
        return 2
    return 0


def pdf_layout_text(pages: list[str]) -> str:
    """El texto de un PDF extraído con `extraction_mode="layout"` (pypdf): los bloques vienen
    separados por líneas en blanco, las tablas en columnas y las listas con sangría."""
    blocks: list[list[str]] = []
    for page in pages:
        current: list[str] = []
        for raw in page.replace("\x00", "").splitlines():
            if not raw.strip() or PAGE_MARK.match(raw.strip()):
                if current:
                    blocks.append(current)
                current = []
                continue
            current.append(raw.rstrip())
        if current:
            blocks.append(current)
    out: list[str] = []
    table: list[list[str]] = []
    first = True

    def close_table() -> None:
        if table:
            out.append("\n".join(table_rows(table)))
            table.clear()

    for i, block in enumerate(blocks):
        cells = [_cells(line) for line in block]
        if all(len(c) >= 2 for c in cells) and (
            not table or abs(len(cells[0]) - len(table[0])) <= 1
        ):
            table.extend(cells)
            continue
        close_table()
        if len(block) == 1:
            nxt = blocks[i + 1][0] if i + 1 < len(blocks) else None
            level = 1 if first and len(block[0].strip()) <= 120 else _is_heading(block[0], nxt)
            first = False
            if level:
                out.append(f"{'#' * level} {block[0].strip()}")
                continue
        first = False
        indents = [len(line) - len(line.lstrip()) for line in block]
        if min(indents) >= 3 and len(block) > 1:
            items: list[str] = []
            for line in block:
                text = line.strip().lstrip("•●◦▪-–* ").strip()
                if items and not items[-1].endswith(TERMINAL):
                    items[-1] += " " + text
                else:
                    items.append(text)
            out.append("\n".join(f"- {item}" for item in items))
            continue
        out.append(_join_lines(block))
    close_table()
    return "\n\n".join(out)


def _join_lines(lines: list[str]) -> str:
    """Un párrafo partido en renglones: se une en uno (y se arreglan las palabras cortadas)."""
    text = ""
    for line in lines:
        part = line.strip()
        if text.endswith("-") and part[:1].islower():
            text = text[:-1] + part
        else:
            text = f"{text} {part}" if text else part
    return text


# -- CSV -------------------------------------------------------------------------------------------
def csv_text(data: bytes) -> str:
    """Una tabla (un catálogo, una lista de precios): una fila por renglón, cada valor con el
    nombre de su columna."""
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    return "\n".join(table_rows(rows))


# -- HTML ------------------------------------------------------------------------------------------
BLOCKS = {
    "p", "div", "section", "article", "header", "footer", "main", "aside", "blockquote",
    "ul", "ol", "dl", "dt", "dd", "form", "figure", "figcaption", "address", "details",
    "summary", "table", "br", "hr",
}  # fmt: skip
SKIP = {"script", "style", "noscript", "svg", "nav", "template", "iframe", "button", "select"}


class HtmlText(HTMLParser):
    """El texto de una página con su estructura: títulos, listas, tablas y párrafos. El menú
    (`<nav>`) y los scripts no cuentan."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.line: list[str] = []
        self.prefix = ""
        self.skip = 0
        self.title = ""
        self.in_title = False
        self.links: list[str] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.table: list[list[str]] | None = None

    def _flush(self) -> None:
        text = " ".join(" ".join(self.line).split())
        if text:
            self.blocks.append(f"{self.prefix}{text}" if self.prefix else text)
        self.line = []
        self.prefix = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.prefix = "#" * int(tag[1]) + " "
        elif tag == "li":
            self._flush()
            self.prefix = "- "
        elif tag == "table":
            self._flush()
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag in BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = False
        elif tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.table is not None and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            for line in table_rows(self.table):
                self.blocks.append(line)
            self.table = None
        elif re.fullmatch(r"h[1-6]", tag) or tag == "li" or tag in BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
            return
        if self.skip or not data.strip():
            return
        if self.cell is not None:
            self.cell.append(data)
        else:
            self.line.append(data)

    def text(self) -> str:
        self._flush()
        out: list[str] = []
        for block in self.blocks:
            # Ítems de lista y filas seguidas van juntos; el resto, como párrafos.
            joined = (
                (block.startswith("- ") or " · " in block)
                and out
                and (out[-1].split("\n")[-1].startswith("- ") or " · " in out[-1].split("\n")[-1])
            )
            if joined:
                out[-1] += "\n" + block
            elif not out or out[-1] != block:
                out.append(block)
        return "\n\n".join(out)
