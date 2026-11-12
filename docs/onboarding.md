# Autoconfiguración (Hito 4): del archivo subido al reporte de preparación

El pipeline toma el historial y los documentos de una empresa y, sin intervención manual, deja:
- los tipos de caso con su volumen;
- los evals;
- las herramientas generadas desde su API;
- un reporte con qué porcentaje de casos resolvería solo el agente.

Nada queda activo: el cliente elige qué tipos y herramientas activar. Decisión 021.

## Desde el panel, sin comandos (Hito 6)

Es el camino de una empresa real. Decisión 026.

1. **El operador crea la empresa.** Panel → **Empresas**: nombre, email y clave del usuario.
2. **El usuario de la empresa ingresa** al panel con ese email y esa clave.
3. **Configuración → Sistema de la empresa:** sube el OpenAPI de su sistema. Es opcional: sin OpenAPI, el asistente solo informa.
4. **Onboarding:** sube el historial de chats y los documentos, y toca **Analizar mi historial**.
   - La pantalla muestra las 6 etapas y se actualiza sola.
   - Si una etapa falla, por ejemplo por la cuota del modelo, el análisis se reintenta desde esa etapa.
5. **Reporte:** qué parte de la atención resolvería el asistente.
6. **Configuración:** activa los tipos de caso, aprueba las herramientas y fija sus límites. Pasa al asistente en vivo en el momento.
7. **Configuración → Sistema de la empresa:** carga la URL base y las credenciales, prueba la conexión y la activa. Hasta entonces las herramientas responden en modo simulado.
8. **Chat de prueba:** el enlace `/c/…` de Configuración abre el chat propio de la empresa.

Test: `test_empresas.py`.

## Correrlo por línea de comandos con la demo de Acme

```
PYTHONPATH=backend python -m app.onboarding.pipeline
```

Por defecto usa:
- el historial de `evals/fixtures/acme/onboarding/`: 100 conversaciones, 40 en un export de WhatsApp, 30 en tickets CSV y 30 en tickets JSON;
- los documentos de `evals/fixtures/acme/documents/`;
- el OpenAPI de `mocks/acme-api/openapi.json`.

Crea el tenant "Acme Store (onboarding)" y deja el reporte en `evals/last_onboarding.md`.

Usa el proveedor del `.env` (con `LLM_PROVIDER=groq`, Groq) y Gemini para los embeddings. Si se corta, por ejemplo porque se agotó la cuota, se vuelve a correr el mismo comando y sigue desde la etapa que faltaba. Dentro del descubrimiento, sigue desde la primera conversación sin resumir.

El historial sintético se regenera con `python evals/fixtures/acme/onboarding/generate_history.py`. Es determinístico y siempre da el mismo resultado.

## Las seis etapas

| Etapa | Qué hace | Test |
|---|---|---|
| 1. Ingesta | WhatsApp (.txt/.zip), tickets (CSV/JSON) y documentos (Markdown, DOCX, PDF, URL) → conversaciones normalizadas y RAG | `test_onboarding_ingest.py`: los 3 formatos dan el mismo esquema |
| 2. Anonimización | Nombres, teléfonos, emails, DNI/CUIT y tarjetas → `<NAME_1>`, `<PHONE_1>`…, antes de guardar | `test_onboarding_anonymize.py`: 0 datos sembrados en la salida |
| 3. Descubrimiento | Resúmenes en lotes, embeddings, agrupamiento y nombres | `test_onboarding_discover.py`: recupera ≥ 7 de 8 tipos conocidos |
| 4. Evals | Casos de prueba desde conversaciones reales, más adversariales | `test_onboarding_evals.py`: cada caso valida y tiene fuente |
| 5. Herramientas | El modelo elige operaciones del OpenAPI y el código arma los manifiestos | `test_onboarding_tools.py`: pasan el contrato contra la API simulada |
| 6. Reporte | Corre los evals contra el agente en un sandbox | `test_onboarding_pipeline.py`: de los archivos al reporte sin intervención |

## Formatos que acepta

- **WhatsApp:** el `.txt` de "Exportar chat" (Android `9/11/26, 14:03 - Nombre: texto` o iOS `[9/11/26, 14:03:12] Nombre: texto`) o un `.zip` con varios. Los avisos de multimedia se ignoran. Los mensajes del número de la empresa son del agente.
- **Tickets CSV:** una fila por mensaje. Hacen falta columnas de ticket, rol del autor y texto, por ejemplo `ticket_id`, `author_type` (`end_user` o `agent`) y `body`, o sus equivalentes en castellano.
- **Tickets JSON:** una lista de tickets con `requester` y `comments` (formato tipo Zendesk).
- **Documentos:** Markdown o texto, DOCX, PDF con texto y URL (decisión 030).
  - Un PDF escaneado, con contraseña o dañado no se guarda: la pantalla avisa por qué y el resto de la carga sigue.
  - En los documentos se reemplazan los números de tarjeta y los DNI. Los emails, teléfonos y CUIT de la empresa quedan como están (decisión 035). Los chats y tickets se anonimizan enteros.
  - Un documento con 3 o más emails o teléfonos distintos parece una lista de personas: no se indexa hasta que la empresa lo confirma en la pantalla Onboarding.
