"""Calidad de Knowledge (Hito 13, tarea 6; decisión 088): ¿la búsqueda trae la fuente correcta?

Indexa los documentos de Acme y Northwind (Markdown) y los de prueba de
`evals/fixtures/knowledge/` (un PDF largo con tablas, un DOCX con títulos, listas y tabla, y un
CSV de productos) con el mismo camino que usa el panel: `read_document` → `index_document`.
Después hace las preguntas de `questions.json` (en lenguaje natural, no copiadas del texto) y
mide, entre los 3 primeros resultados:

- **fuente@3**: está el documento correcto;
- **respuesta@3**: algún fragmento trae la respuesta (el dato puntual: "60 días corridos", el
  SKU del producto). Mide que la extracción y los fragmentos no corten la política a la mitad.

Embeddings reales si hay clave (VOYAGE_API_KEY o GEMINI_API_KEY); si no, los simulados. No llama
a ningún modelo. Corre contra DATABASE_URL_STAGING (o DATABASE_URL) en una transacción que se
revierte: no deja datos.

Uso: python evals/knowledge_recall.py [--simulated] [--json salida.json]
"""

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (HERE.parent / "backend", HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import httpx  # noqa: E402
from simulated.voyage import voyage_transport  # noqa: E402

from app.config import load_dotenv  # noqa: E402
from app.db.connection import connect  # noqa: E402
from app.db.migrate import migrate  # noqa: E402
from app.llm.client import PostgresUsageRecorder  # noqa: E402
from app.llm.embedder import VoyageEmbedder  # noqa: E402
from app.llm.factory import embedder_from_env, embedding_backend  # noqa: E402
from app.onboarding.ingest import read_document  # noqa: E402
from app.rag.indexer import index_document  # noqa: E402
from app.rag.search import hybrid_search  # noqa: E402

FIXTURES = HERE / "fixtures"
KNOWLEDGE = FIXTURES / "knowledge"
FILES = [
    *sorted((FIXTURES / "acme" / "documents").glob("*.md")),
    *sorted((FIXTURES / "northwind" / "documents").glob("*.md")),
    KNOWLEDGE / "acme_manual_de_politicas.pdf",
    KNOWLEDGE / "acme_manual_de_atencion.docx",
    KNOWLEDGE / "acme_catalogo.csv",
]
FORMATS = {".pdf": "PDF", ".docx": "DOCX", ".csv": "CSV", ".md": "Markdown"}


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulated", action="store_true", help="embeddings simulados")
    parser.add_argument("--json", help="guarda el resultado en este archivo")
    args = parser.parse_args()
    load_dotenv()
    url = os.environ.get("DATABASE_URL_STAGING") or os.environ.get("DATABASE_URL")
    migrate(url)
    backend = None if args.simulated else embedding_backend()
    questions = json.loads((KNOWLEDGE / "questions.json").read_text(encoding="utf-8"))
    formats = {path.stem: FORMATS[path.suffix] for path in FILES}
    conn = connect(url)
    try:
        row = conn.execute(
            "insert into tenants (name) values ('knowledge') returning id"
        ).fetchone()
        assert row is not None
        tenant = row[0]
        recorder = PostgresUsageRecorder(conn)
        embedder = (
            embedder_from_env(recorder)
            if backend
            else VoyageEmbedder(
                recorder, "sim", http_client=httpx.Client(transport=voyage_transport())
            )
        )
        chunks = 0
        lines: dict[str, set[str]] = {}
        for path in FILES:
            document = read_document(path.read_bytes(), path.name)
            index_document(
                conn, tenant, embedder, title=document.title, source=path.stem, text=document.text
            )
            lines[path.stem] = {_plain(line) for line in document.text.splitlines() if line.strip()}
        # Cortes: un fragmento que empieza o termina a mitad de un renglón del documento (una
        # oración o una fila de tabla partida en dos).
        cut = 0
        for source, text in conn.execute(
            "select source, text from chunks where tenant_id = %s", (tenant,)
        ).fetchall():
            body = [line for line in text.splitlines()[1:] if line.strip()]
            ends = [_plain(body[0]), _plain(body[-1])] if body else []
            cut += any(end not in lines[source] for end in ends if not end.startswith("#"))
        count = conn.execute(
            "select count(*) from chunks where tenant_id = %s", (tenant,)
        ).fetchone()
        chunks = int(count[0]) if count else 0
        by_format: dict[str, list[int]] = {}
        misses: list[str] = []
        for item in questions:
            found = hybrid_search(conn, tenant, embedder, item["q"], k=3)
            answer = _plain(item["answer"])
            right = [r for r in found if r.source == item["doc"]]
            source_ok = bool(right)
            hits = [r for r in right if answer in _plain(r.text)]
            answer_ok = bool(hits)
            first_ok = (
                bool(found) and found[0].source == item["doc"] and answer in _plain(found[0].text)
            )
            # Contexto: el fragmento con la respuesta trae también el título de su sección.
            section = _plain(item.get("section", ""))
            context_ok = answer_ok and (not section or section in _plain(hits[0].text))
            stats = by_format.setdefault(formats[item["doc"]], [0, 0, 0, 0, 0])
            for i, ok in enumerate((True, source_ok, answer_ok, first_ok, context_ok)):
                stats[i] += ok
            if not (answer_ok and context_ok):
                misses.append(f"{item['q']} -> {[r.source for r in found]}")
        total = [sum(v[i] for v in by_format.values()) for i in range(5)]
        label = {"voyage": "Voyage", "gemini": "Gemini"}.get(str(backend), "simulados")
        print(f"Embeddings {label} · {len(FILES)} documentos · {chunks} fragmentos")
        print(f"Fragmentos que cortan una oración o una fila: {cut}/{chunks}")
        print("| Formato | Preguntas | Fuente@3 | Respuesta@3 | Respuesta@1 | Con su sección |")
        print("|---|---|---|---|---|---|")

        def cell(ok: int, n: int) -> str:
            return f"{ok}/{n} ({ok / n:.0%})"

        for name, (n, *rest) in sorted(by_format.items()):
            print(f"| {name} | {n} | " + " | ".join(cell(ok, n) for ok in rest) + " |")
        n, *rest = total
        print(f"| **Total** | {n} | " + " | ".join(cell(ok, n) for ok in rest) + " |")
        for miss in misses:
            print("  falla:", miss)
        if args.json:
            Path(args.json).write_text(
                json.dumps(
                    {"embeddings": label, "chunks": chunks, "cut": cut, "by_format": by_format,
                     "misses": misses},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )  # fmt: skip
        return 0
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
