# 088 — Knowledge: lo que el agente sabe, claro y confiable

**Contexto.** Knowledge es lo más importante del producto: el agente responde solo con lo que está ahí. La pantalla de Onboarding mezclaba la carga de archivos, el sitio y el análisis del historial, no mostraba qué había aprendido de cada documento, y un archivo que fallaba solo dejaba un aviso que desaparecía. La extracción tenía tres problemas:
- en un PDF, cada página era un solo párrafo, y en un DOCX todo el documento;
- los títulos, las listas y las tablas se perdían;
- los fragmentos se cortaban por palabras a mitad de una política.

## Qué se decidió

**1. Una pantalla, todo lo de Onboarding y más claro** (`/panel/knowledge`). Se conserva todo lo que se podía hacer en Onboarding:
- subir PDF, DOCX, TXT, MD y CSV, y exportes de chats y tickets;
- los nombres del negocio en los chats;
- la anonimización (sin cambios);
- los documentos con datos personales, que esperan confirmación;
- el sitio;
- el análisis del historial: tipos de caso, pruebas, acciones y reporte;
- el aviso de "terminó", con links al reporte de preparación y a aprobar temas y acciones.

Lo nuevo:
- **Subir arrastrando.** Cada archivo se sube por separado y muestra su estado mientras se procesa: Processing → Ready, Failed (con el motivo exacto y qué hacer) o Needs you (datos personales). Sin JavaScript, el formulario de siempre.
- **Los que fallan quedan en la lista** (`documents.status = 'failed'`, `error`) hasta que se suben bien o se quitan.
- **Qué aprendió** (`/panel/knowledge/doc/{id}`): las secciones que busca el agente, cada una con su título, y el texto extraído completo (`documents.text`, migración 0037).
- **Reemplazar** un documento lo reindexa aunque el archivo nuevo tenga otro nombre. **Borrarlo** lo saca de las respuestas en el momento.
- **Ask your knowledge:** una pregunta, la respuesta y las fuentes exactas.
  - Con modelo (`LLMClient`, registra el uso), la respuesta se arma solo con las fuentes numeradas, que son dato y no instrucción, y cita cuáles usó.
  - Sin modelo, muestra los pasajes que comparten palabras con la pregunta.
  - Si no hay nada, dice que el conocimiento no lo responde y que el agente derivaría.
- **Problemas para arreglar**, sin modelo:
  - **Contradicciones:** documentos que dan plazos distintos para lo mismo (devolución, cambio, garantía, entrega). Se compara el plazo general de cada documento, no las filas de tabla, que son casos puntuales.
  - **Sin cobertura:** las preguntas que el agente no pudo responder (el análisis de Insights → Results, decisión 083), agrupadas por tema.
  - **Información vieja:** sin actualizar hace más de un año, o que da por vigente un año pasado ("precios vigentes hasta … 2024").

**2. El sitio, con sus páginas** (tablas `sites` y `site_pages`, migración 0037):
- **Find pages** lee el sitio y usa todas las páginas. La empresa desmarca las que no quiere, y esas dejan de usarse en el momento.
- **Sync now**, o **cada semana**: el worker de la API corre `knowledge.sync_due`, idempotente. Una página nueva entra marcada; las que la empresa sacó siguen afuera.
- Se muestra la fecha de la última sincronización y el error, si falló.
- **El rastreo prioriza** las páginas de ayuda, políticas, envíos, devoluciones y contacto, y deja para el final las listas de productos.
- **El menú y el pie** se repiten en cada página y tapaban lo que importa. Una línea que aparece en la mitad de las páginas o más queda solo en la de inicio.

**3. Extracción con estructura** (`app/onboarding/extract.py`). Todo formato pasa a un texto tipo Markdown:
- **PDF:** pypdf en modo `layout`. Los bloques separados por líneas en blanco son párrafos (se unen los renglones y las palabras cortadas). Las líneas en columnas son filas de tabla. Los bloques con sangría son listas. Una línea corta y sola es un título: con numeración, del nivel que dice ("1.1" → `###`). Se descartan los números de página.
- **DOCX:** los estilos Title y Heading 1, 2… son títulos. Los párrafos con numeración son listas, numeradas o con viñetas según `numbering.xml`. Las tablas son filas.
- **HTML:** `<h1>`…`<h6>`, `<li>`, tablas y bloques. Sin `<nav>` ni scripts.
- **Tablas y CSV:** una fila por renglón, cada valor con el nombre de su columna (`Zona: CABA · Costo del envío: $ 3.900 · …`). Así una fila se entiende sola.

**4. Fragmentos que no cortan** (`rag.indexer.chunk_text`):
- Por sección. Cada fragmento empieza con los títulos de su sección (`## 3. Devoluciones › 3.1 Plazos…`); el del documento se antepone al indexar, como antes.
- Nunca corta un párrafo, un ítem de lista ni una fila de tabla, salvo que solo ya sea más largo que un fragmento (900 caracteres). En ese caso corta por oraciones.
- Una sección muy corta (menos de 240 caracteres) se junta con la siguiente.

**Medición** (`evals/knowledge_recall.py`, resultados en `docs/calidad.md`). Con embeddings reales:
- 0 fragmentos cortados (antes 25 de 48);
- la respuesta en el fragmento con su sección en 46 de 46 (antes 38);
- Fuente@3 y Respuesta@3 en 100%.

Con los simulados, Respuesta@3 baja de 44 a 40: está explicado ahí.

## Sin verificar

- La extracción de PDFs reales con tablas complejas (celdas en varios renglones), escaneados con OCR o en dos columnas. La heurística de títulos puede tomar como título una línea corta suelta.
- La re-sincronización semanal en Render: el worker de la API la corre en cada vuelta, como el informe semanal.
