# Calidad del agente: 60 conversaciones realistas de Acme

**Rama:** `calidad` (sale de `hito-3`). **Modelo:** Groq `openai/gpt-oss-20b`, en el agente y en el clasificador. **Embeddings:** Gemini.

Las conversaciones están en `evals/fixtures/acme/quality_conversations.json`: 60 conversaciones escritas como escribe la gente por WhatsApp, sin tildes, con abreviaturas, errores de tipeo, mensajes partidos, varias preguntas juntas, cambios de tema, enojo e ironía.

Cubren estas situaciones:
- Saludos y "¿qué podés hacer?".
- Consultas de varios turnos.
- Pedidos fuera de política.
- Preguntas sin respuesta en los documentos.
- Inyecciones de prompt.
- Pedidos de datos de otros clientes.

Se corren con `python evals/quality.py`. El runner (`evals/quality.py`) califica el último turno: acción, herramientas, palabras clave y verificación. También controla que ningún turno derive en una charla y que no aparezcan datos prohibidos. Cada falla se atribuye a una causa a partir de la auditoría de cada turno: clasificador, RAG, prompt, herramienta o guardián.

## Medición base (antes de cualquier arreglo)

**26 de 60 resueltas (43%).** Sin fallas críticas: no hubo datos de otro cliente, inyecciones exitosas ni respuestas inventadas.

| Categoría | Resueltas |
|---|---|
| Saludo | 0/4 |
| Agradecimiento | 0/2 |
| Charla | 0/2 |
| ¿Sos un bot? / ¿Qué podés hacer? | 0/5 |
| Fuera de tema | 0/5 |
| Consulta general | 7/12 |
| Pedido | 5/8 |
| Varias preguntas | 1/3 |
| Cambio de tema | 1/2 |
| Enojo e ironía | 1/4 |
| Pide una persona | 2/2 |
| Fuera de política | 1/3 |
| Sin respuesta en los documentos | 3/3 |
| Inyección de prompt | 3/3 |
| Datos de otro cliente | 2/2 |

### Fallas por causa

| Causa | Casos | Qué pasa |
|---|---|---|
| Clasificador | 22 | Los saludos, la charla, "¿sos un bot?", "¿qué podés hacer?" y los temas fuera de tema no tienen tipo de caso: caen en `otro` y el agente deriva (18). Las consultas generales sin tipo propio (cuánto tarda un envío, si es gratis, guía de talles, cambios en general) también derivan (4) |
| Prompt | 10 | 5 terminan en "El modelo no pudo responder": Groq rechaza 3 salidas inválidas seguidas. 2 los frena el control de fundamento: "20:00" se lee como el número "00", y una respuesta cita la herramienta `consultar_pedido` como fuente. 3 derivan en lugar de explicar ("el pedido ya fue despachado", "el reembolso está fuera de plazo") |
| RAG | 1 | "Me arrepentí, ¿puedo devolverlo?" se clasifica como reembolso y pide verificar la identidad en lugar de explicar los 10 días de arrepentimiento |
| Guardián | 1 | Un mensaje partido en 4 turnos ("hola", "una consulta", "el pedido 1002", "ya lo despacharon?"): el último turno vuelve a pedir el número de pedido |

## Arreglos, en orden de impacto

| # | Arreglo | Resueltas | Cambio |
|---|---|---|---|
| 0 | Medición base | 26/60 (43%) | — |
