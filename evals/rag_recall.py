"""Mide Recall@1 y Recall@5 del RAG híbrido con las preguntas de Acme.

Con VOYAGE_API_KEY usa embeddings reales de Voyage; si no, con GEMINI_API_KEY los de Gemini; si
no, los simulados. No deja datos: corre en una transacción que se revierte.

Uso: python evals/rag_recall.py
"""

import json
import sys
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
from app.demo.acme import ACME_DIR, index_acme_documents  # noqa: E402
from app.llm.client import PostgresUsageRecorder  # noqa: E402
from app.llm.embedder import VoyageEmbedder  # noqa: E402
from app.llm.factory import embedder_from_env, embedding_backend  # noqa: E402
from app.rag.search import hybrid_search  # noqa: E402


def main() -> int:
    load_dotenv()
    migrate()
    backend = embedding_backend()
    questions = json.loads((ACME_DIR / "rag_questions.json").read_text(encoding="utf-8"))
    conn = connect()
    try:
        row = conn.execute("insert into tenants (name) values ('recall') returning id").fetchone()
        assert row is not None
        recorder = PostgresUsageRecorder(conn)
        embedder = (
            embedder_from_env(recorder)
            if backend
            else VoyageEmbedder(
                recorder, "sim", http_client=httpx.Client(transport=voyage_transport())
            )
        )
        index_acme_documents(conn, row[0], embedder)
        at1 = at5 = 0
        for item in questions:
            sources = [r.source for r in hybrid_search(conn, row[0], embedder, item["question"])]
            at1 += sources[:1] == [item["expected"]]
            at5 += item["expected"] in sources
            if item["expected"] not in sources:
                print(f"  falla: {item['question']} -> {sources}")
        total = len(questions)
        labels = {"voyage": "Voyage reales", "gemini": "Gemini reales"}
        print(f"Embeddings {labels.get(str(backend), 'simulados')}:")
        print(f"  Recall@1 = {at1}/{total} ({at1 / total:.0%})")
        print(f"  Recall@5 = {at5}/{total} ({at5 / total:.0%})  (meta de la spec: >= 90%)")
        return 0 if at5 / total >= 0.9 else 1
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
