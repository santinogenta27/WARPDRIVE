# 006 — RAG híbrido: fragmentos, consulta y fusión

**Estado:** adoptada durante la corrida sin supervisión (opción más simple). Revisar.

## Decisiones

- **Fragmentos:** párrafos agrupados hasta 700 caracteres; el título del documento se antepone a cada fragmento (mejora la búsqueda y da contexto). Los títulos Markdown no forman fragmentos solos.
- **Full-text:** `tsvector` en español generado por Postgres. La consulta se arma en modo OR con las raíces de la pregunta (en modo AND, una pregunta natural casi nunca coincide entera). Orden por `ts_rank_cd`.
- **Vectorial:** distancia coseno con índice HNSW sobre `vector(1024)`.
- **Fusión:** Reciprocal Rank Fusion con k = 60 sobre los 20 mejores de cada búsqueda; se devuelven los 5 primeros con id de fragmento, documento, título y fuente (para citar).
- **Documentos de Acme:** 11 políticas escritas a mano (la spec pide 20 generadas con Opus; queda pendiente con la clave). 23 preguntas de prueba en `evals/fixtures/acme/rag_questions.json`.

## Resultado y advertencia

Con embeddings **simulados** (vectores léxicos deterministas, `evals/simulated/voyage.py`): Recall@1 = 22/23, Recall@5 = 23/23. Ese número mide el pipeline (indexado, full-text, fusión, aislamiento por tenant), no la calidad semántica de Voyage. Con `VOYAGE_API_KEY` hay que volver a medir: el mismo test corre con el cliente real cambiando solo el transporte.
