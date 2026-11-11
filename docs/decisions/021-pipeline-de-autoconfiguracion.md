# 021 — Pipeline de autoconfiguración (Hito 4)

**Estado:** adoptada (9 de noviembre de 2026). Revisar.

## Contexto

Las tareas 14 a 19 de la spec llevan a una empresa del archivo subido al reporte de preparación, en seis etapas idempotentes. La spec prevé Haiku para resumir y Opus para generar evals y herramientas. Hoy no hay claves de Anthropic: se usa Groq gratis, con límites de 1000 pedidos por día y 8000 tokens por minuto por modelo.

## Decisiones

- **Etapas y avance** (`app/onboarding/pipeline.py`):
  - Ingesta, anonimización, descubrimiento, evals, herramientas y reporte.
  - Cada etapa guarda su resultado en `onboarding_runs.stages` (migración 0012). Si una falla, por ejemplo porque se agotó la cuota, la próxima corrida retoma desde la primera que falte.
  - Dentro del descubrimiento, el avance se guarda por conversación resumida, así no se vuelve a resumir.
- **Ingesta** (`ingest.py`):
  - Export de WhatsApp en `.txt` (Android o iOS) o `.zip`.
  - Tickets de helpdesk en CSV o JSON, con columnas habituales en inglés o castellano.
  - Documentos en Markdown, DOCX (se lee el XML, sin dependencias nuevas) y URL (HTML).
  - **PDF todavía no:** haría falta una dependencia (`pypdf`). Se pide subirlo como DOCX o Markdown.
  - Cada archivo se identifica por su hash, así subirlo dos veces no duplica.
- **Anonimización** (`anonymize.py`):
  - Al guardar, antes de que el texto toque la base o un modelo.
  - Datos que cubre: emails, tarjetas (con Luhn), CUIT, DNI (con puntos o con la palabra "DNI"), teléfonos argentinos y nombres.
  - Los nombres salen de los participantes que trae la fuente y de "soy X" o "me llamo X".
  - Los marcadores son tipados y consistentes dentro de la conversación (`<NAME_1>`).
  - Se conservan pedidos, montos y fechas.
  - La etapa de anonimización vuelve a revisar la base y falla si encuentra un dato en claro.
- **Descubrimiento** (`discover.py`):
  - Resúmenes en lotes de 10 conversaciones (intención, resultado y si hubo que actuar en un sistema).
  - Embeddings de los resúmenes con Gemini.
  - **Agrupamiento aglomerativo de enlace promedio** con coseno, eligiendo el corte de mejor silueta. Los grupos de menos de 3 son ruido.
  - Cumple el rol de HDBSCAN sin sumar `scikit-learn` ni `hdbscan`. Para cientos o miles de conversaciones alcanza; con muchas más, conviene HDBSCAN.
  - Los tipos se guardan inactivos.
- **Evals** (`evalgen.py`):
  - El modelo convierte hasta 3 conversaciones por tipo en casos.
  - Se valida el esquema y que la conversación fuente exista. Si el modelo inventa una fuente, el caso se descarta.
  - Se suman 4 casos adversariales: inyección, datos en masa, fuera de política y datos de otro cliente.
- **Herramientas** (`toolgen.py`):
  - El modelo elige qué operaciones del OpenAPI necesita cada tipo. **El manifiesto lo arma el código** desde la operación, no el modelo.
  - Esquema estricto.
  - Riesgo: un GET es bajo; dinero es alto, con aprobación siempre; cancelar o borrar es medio; el resto, bajo.
  - Control de dueño si el recurso tiene teléfono del cliente.
  - Idempotencia en los POST.
  - Las operaciones sobre clientes o usuarios no se ofrecen al agente.
  - Todo queda sin aprobar.
  - Un contrato verifica que la respuesta esté documentada y cumpla su esquema.
- **Reporte** (`report.py`):
  - Corre los evals contra el agente con lo descubierto, en un sandbox. Las herramientas responden con datos armados desde el esquema del OpenAPI.
  - Para probarlas, tipos y herramientas se activan dentro de un savepoint que se revierte: el tenant queda como estaba y el cliente decide qué activar.
  - Por tipo: volumen, evals, resueltos, porcentaje, errores y qué falta.
  - La resolución estimada se pondera por volumen.
- **Modelo** (`model.py`):
  - Cada pedido fuerza una herramienta con esquema, así la salida es JSON validable.
  - Con Groq, `gpt-oss-20b`.
  - Los textos llegan anonimizados.
- **Límites de Groq:** los resúmenes van en lotes de 10, para usar pocos pedidos. La primera corrida es con las 100 conversaciones de Acme.

## Pendiente

- PDF (dependencia `pypdf`) y más formatos de helpdesk.
- Correr cada etapa como job de la cola. Hoy el pipeline corre por CLI y en el test, con el mismo avance guardado.
- Pantalla del panel para que el cliente active tipos y herramientas (Hito 5).
- Los costos del modelo durante el reporte se revierten con el savepoint. Hay que registrarlos aparte.
