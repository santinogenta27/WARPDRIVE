# Listo para vender — auditoría del Hito 9

17 de noviembre de 2026, sobre `main` (con los hitos 7 y 8 mergeados). Esfuerzo: S (horas), M (un día), L (varios días).

| # | Requisito | Estado | Evidencia | Esfuerzo |
|---|---|---|---|---|
| 1 | Alta self-service: registro, verificación de email, admin, invitaciones con roles | Falta | Solo el operador crea empresas (`companies/create.py`, decisión 026); un usuario por empresa, sin roles (`panel/auth.py`) | L |
| 2 | Carga de conocimiento desde el panel | Parcial | Onboarding acepta PDF, DOCX, MD, TXT y chats/tickets (CSV, JSON, WhatsApp) con avance por etapas (`onboarding/ingest.py`, `test_onboarding_ingest.py`); tarjetas y DNI anonimizados en documentos (decisión 035, `test_datos_sensibles.py`). Falta: ver, reemplazar y borrar documentos indexados; páginas del sitio; CSV como documento | M |
| 3 | Acciones reales sin programar: OpenAPI con credenciales cifradas y prueba; Shopify y Tiendanube | Parcial | OpenAPI, credenciales cifradas, prueba y activación (`companies/connection.py`, `test_empresas.py`). Falta probar herramienta por herramienta y los conectores de Shopify y Tiendanube | L |
| 4 | Reglas en el panel: herramientas, montos, aprobación, identidad, horario, derivación | Parcial | Herramientas: aprobar y límites de monto (`/configuracion/herramientas`, `companies/review.py`); el guardián aplica identidad y montos (`policy/guardian.py`). Falta: horario de atención, a quién derivar, exigir aprobación e identidad por herramienta desde el panel | M |
| 5 | Tono y marca | Parcial | Nombre, color, logo y sugerencias del widget (decisión 031, `test_widget.py`); idioma por empresa (decisión 036). Falta: saludo propio | S |
| 6 | Prueba antes de salir: playground, evals del historial, umbral para "Go live" | Parcial | "Detrás de escena" (decisión 041, `test_detras_de_escena.py`); evals generados del historial y reporte de preparación (`onboarding/evalgen.py`, `onboarding/report.py`). Falta: playground en el panel y umbral que habilite "Go live" | M |
| 7 | Instalación: snippet y guías | Parcial | Snippet del widget y dominios permitidos (`docs/widget.md`). Falta: snippet copiable en el panel y guías de Shopify, WordPress y Tiendanube | S |
| 8 | Bandeja humana con aviso por email | Parcial | Casos derivados primero, aprobaciones y respuesta desde el panel (`panel/routes.py`, `test_panel.py`). Falta: aviso por email al equipo cuando hay un derivado o una aprobación | S |
| 9 | Modelos y costos: API key por empresa, tope mensual, métricas | Parcial | Métricas de resolución, derivación, CSAT y costo (`obs/metrics.py`, `test_observabilidad.py`, `test_csat.py`). Falta: key por empresa cifrada y tope de gasto con aviso al 80 % y derivación al 100 % | M |
| 10 | Calidad: nunca inventa, nunca promete, adversariales | Parcial | Fundamento (`agent/grounding.py`); acción afirmada sin herramienta se rechaza una vez y después deriva con motivo en "Why" (`agent/conduct.py`, decisión 040). Falta: "enviado"/"sent" y pasivas ("está aplicado"); una aprobación pendiente no respalda "reembolsado"; adversariales de fuera de tema, datos de otro cliente e idiomas mezclados en Acme y Northwind | S |
| 11 | Producción mínima | Parcial | Borrado de una empresa (decisión 034); alertas por email (decisión 033); límites por IP y sesión (decisión 019). Falta: términos y privacidad, exportar datos, backups documentados, rotación de secretos en `docs/render.md` | M |

## Estado al cerrar el hito

El código de los once requisitos está en las ramas `h9-…`, con sus tests. Lo que no se puede verificar sin cuentas o personas reales sigue abierto:

| # | Requisito | Estado | Qué falta verificar |
|---|---|---|---|
| 1 | Alta self-service | Hecho en código | Envío real del email de confirmación (Resend) |
| 2 | Carga de conocimiento | Hecho | Sitios que arman el texto con JavaScript; OCR |
| 3 | Shopify y Tiendanube | Parcial | Probarlo contra una tienda real: los tests usan respuestas de la documentación |
| 4 | Reglas en el panel | Hecho | — |
| 5 | Tono y marca | Hecho | Dos idiomas a la vez en una empresa |
| 6 | Probar antes de salir | Hecho | Revisar a mano una muestra de evals generados |
| 7 | Instalación | Hecho en código | Los pasos en una tienda real de cada plataforma |
| 8 | Bandeja con aviso | Hecho en código | Envío real del email (Resend) |
| 9 | Key propia y tope | Parcial | Una key real de cada proveedor; precios de `gpt-5-mini` |
| 10 | Calidad | Hecho, en simulado | Medir con Groq real (cuota) |
| 11 | Producción mínima | Parcial | Revisión del abogado; primer backup y restauración |

**Evals simulados:** Acme y Northwind en verde, sin regresiones, en cada una de las diez ramas.

**De punta a punta:** cada paso de una empresa nueva tiene su test (registro, carga, reglas, playground, salir en vivo, widget), pero nadie lo recorrió entero con una empresa real. Es la primera prueba para hacer antes de vender.

## Orden de trabajo

Tal como pide el Hito 9: 10 → 9 → 2 → 4 → 5 → 7 → 8 → 6 → 11 → 3 y 1. Ninguno está hecho entero, así que no se saltea ninguno. Las ramas van apiladas (decisión 047).
