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

## Por qué la serie de mediciones cambió de modelo

El plan gratuito de Groq permite 200.000 tokens por día y por modelo. Cada corrida de las 60 conversaciones usa unos 160.000. La medición base y los evals agotaron `gpt-oss-20b` el mismo día.

Para que todas las mediciones fueran comparables, la serie se hizo entera con otra configuración de Groq: agente `qwen/qwen3.8-27b` y clasificador `openai/gpt-oss-120b`. Con esa configuración se volvió a medir la base, con el código de antes de los arreglos.

La caché de respuestas (`app/llm/cache.py`) y la de embeddings (`app/llm/embedding_cache.py`) evitan pagar dos veces las llamadas que un arreglo no cambia.

Aun así, la cuota no alcanzó para medir cada arreglo sobre las 60:
- **El arreglo 1** se midió sobre las primeras 32 conversaciones, que es donde están todas las categorías que toca.
- **El arreglo 2**, que cambia el prompt y por eso todas las llamadas del agente, se midió sobre un subconjunto fijo de 13: las 8 a las que apunta y 5 de control. Entre las de control están la inyección de prompt y el pedido de datos de otro cliente.

## Arreglos, en orden de impacto

Cada arreglo se compara contra la medición anterior **sobre las mismas conversaciones**.

| # | Arreglo | Commit | Conversaciones medidas | Antes | Después | Regresiones |
|---|---|---|---|---|---|---|
| 0 | Base, serie qwen + gpt-oss-120b | — | 60 | — | 34/60 (57%) | — |
| 1 | Charla y consultas generales sin derivar (clasificador con `conversacion_general` y `consulta_general`; la charla solo puede responder) | `ef91237` | 32 | 11 | **29** | Ninguna |
| 2 | Prompt `system_v2` (derivar solo si corresponde y explicar los "no se puede"); control de fundamento sin falsos rechazos (horas en punto, herramientas o números de pedido citados como fuente) | `c34dbf3`, `dacb48c` | 13 | 5 (base) | **10** | Ninguna |
| 3 | El clasificador trata como charla los pedidos ajenos a la tienda ("¿me hacés la tarea?") y las quejas generales ("nunca responden") | `160f1a2` | 13 | 10 | **12** | Ninguna |
| 4 | La verificación se pide recién cuando el agente intenta usar datos de la cuenta y el guardián lo niega, no por el tipo de caso | `182a3fb` | 13 | 12 | **13** | Ninguna |

**Arreglo 1:** pasaron a resolverse 18 conversaciones: los 4 saludos, los 2 agradecimientos, la charla, las 5 de "¿sos un bot?" o "¿qué podés hacer?", 4 de las 5 fuera de tema y 2 consultas generales (envíos).

**Arreglo 2:** pasaron a resolverse 5 de las 8 a las que apuntaba:
- "el pedido ya se despachó" y la ironía ("una semana esperando");
- la devolución fuera de plazo, que ahora explica los 10 días;
- la cancelación que pide aprobación, que antes el control de fundamento rechazaba porque citaba el número de pedido;
- dos preguntas en un mismo mensaje.

Las 5 de control siguen bien.

**Arreglo 3:** "¿me hacés la tarea de historia?" y "ustedes no responden nunca" caían en `otro` y derivaban; ahora reciben la respuesta de charla.

Probé mandar todo `otro` a la charla y lo descarté: rompe el caso guionado 07, "¿hacen arreglos de ruedo?", que tiene que derivar porque la respuesta no está en los documentos. El arreglo quedó en la descripción de `conversacion_general`.

**Arreglo 4:** "me arrepentí, ¿puedo devolverlo?", de un cliente no verificado, pedía el email porque el tipo de caso (reembolso) tiene herramientas que exigen verificación. Ahora responde con la política de 10 días.

Si el agente intenta consultar o modificar un pedido, el guardián lo niega igual que antes y recién ahí se pide el código. No cambió ningún test de seguridad: verificación, guardián, inyección y datos de otro cliente pasan sin tocarlos.

## Dónde terminó

Las 13 del subconjunto quedaron resueltas y me detuve: ya no había fallas para atacar. No hubo ninguna regresión en ninguna medición, y cero fallas críticas en todas: ni datos de otro cliente, ni inyecciones exitosas, ni respuestas inventadas.

**Pendiente:** una corrida final de las 60 con los cuatro arreglos (`python evals/quality.py --restart`, con `LLM_PROVIDER=groq`). No entró en la cuota diaria. Con un día de cuota de Groq, o con Claude, se hace de una vez.
