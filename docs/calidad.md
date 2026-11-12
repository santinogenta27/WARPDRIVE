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

## Hito 6: arreglos de comportamiento y estado de la medición (10 de noviembre de 2026)

Decisión 027. Esta sección dice qué se arregló, qué quedó probado y qué falta medir con el modelo real.

### Qué cambió

| Cambio | Dónde vive | Cómo quedó probado |
|---|---|---|
| Una amenaza de denuncia o de acción legal deriva siempre | Código | `test_errores_del_reporte.py` y caso guionado 14 |
| Si falta un dato que el cliente puede dar, pedirlo en lugar de derivar | Prompt `system_v4` e instrucciones del tipo | Caso guionado 12, con el modelo simulado |
| Si los documentos cubren el tema, responder aunque no nombren lo puntual (Mercado Pago) | Prompt `system_v4` e instrucciones del tipo | Caso guionado 13, con el modelo simulado |
| Llamada de Groq con el nombre de la herramienta roto: se recupera | Código (`GroqTransport`) | `test_groq.py` |
| Reapertura a los 7 días y conteo para el cobro | Código | `test_reapertura.py` |

Con el modelo simulado, los 14 casos guionados dan 14 de 14 (`python evals/runner.py --simulated`). Eso prueba el cableado y lo que vive en código. No mide a Groq.

### Lo que se vio con Groq real

- **`make evals`, a las 21:20:** corrieron 3 de los 14 casos y los 3 derivaron. `gpt-oss-20b` generó el nombre de la herramienta con un marcador pegado (`responder<|channel|>commentary`) tres veces seguidas en cada caso. Las respuestas eran correctas: el problema era solo el nombre.
- Después de ese tercer caso, Groq cortó por cuota: pidió esperar 604 s.
- Ese hallazgo dio origen al arreglo de `GroqTransport`. Todavía no se midió con Groq si alcanza.
- **`RUN_GROQ_TESTS=1 GROQ_REPEAT=5`, a las 22:11:** 0 de 5, y ninguno llegó a medir el flujo.
  - Tres intentos cortaron por cuota: Groq pidió esperar entre 747 y 830 s.
  - Dos intentos esperaron la cuota con la transacción abierta y Neon cerró la conexión por inactividad. Una espera de más de 10 minutos no sirve dentro de un turno.
- **`RUN_GROQ_TESTS=1 GROQ_REPEAT=5`, el 11 de noviembre a las 03:03, ya con todos los arreglos:** **3 de 5 pasaron y los otros 2 no llegaron a correr.**
  - Los 3 que corrieron hicieron el flujo completo: verificación por código, pedido de aprobación y voseo.
  - Los 2 restantes cortaron por cuota: Groq pidió esperar 532 y 704 s.
  - A los 15 minutos repetí esos 2: cortaron otra vez por cuota (530 y 917 s), sin correr.
  - A las 05:05, pasada la medianoche del Pacífico, repetí los 5: **1 pasó y 4 cortaron por cuota** (241 a 795 s).
  - **En total, 4 de 4 entre los que midieron, no un 5 de 5 seguido.** No hubo ninguna falla de comportamiento.
- **La cuota no se renueva de golpe a medianoche.** Es una ventana móvil: se liberó cerca de un intento del flujo cada hora y media. Cinco intentos seguidos necesitan varias horas sin que nadie use el modelo, tampoco el chat de Render.
- La cuota de `gpt-oss-20b` es la misma que usa el chat de Render. No insistí más para no dejar la demo sin modelo.

### Pendiente, con cuota disponible

En este orden. Conviene correrlos después de varias horas sin uso del chat de Render, o con otra clave de Groq.

1. **Flujo de reembolso, 5 de 5 en una sola corrida** (hoy hay 4 de 4, en tres corridas):
   `RUN_GROQ_TESTS=1 GROQ_REPEAT=5 python -m pytest backend/tests/test_groq_real.py`
   Sin `GROQ_MAX_WAIT`: con el valor por defecto, una espera larga corta enseguida en lugar de dejar la conexión colgada.
2. **Los 14 casos guionados:** `python evals/runner.py --restart`. Mide los casos 12, 13 y 14 y deja la corrida en el historial de evals de la página Métricas.
3. **Las 60 conversaciones de calidad:** `python evals/quality.py --restart`, con `LLM_PROVIDER=groq`. El resultado final va en esta sección.

Si el flujo de reembolso no da 5 de 5, el primer lugar para mirar es el log de `app.llm.groq`: dice si el modelo sigue generando nombres rotos que no se pudieron recuperar.

## Arreglos de la prueba de modelos y nueva medición (12 de noviembre de 2026)

Sigue a la sección "Prueba de modelo del agente" (PR #10). Esa sección encontró derivaciones que no venían del modelo y respuestas que llegaban al cliente con JSON. Esta sección los arregla y vuelve a medir.

### Qué cambió

Cada cambio tiene un test de aceptación con el modelo simulado en `backend/tests/test_calidad_agente.py`.

| # | Cambio | Dónde |
|---|---|---|
| 1 | El control de fundamento compara los números sin ceros adelante: "9:30" con la fuente "09:30" y el día 5 con "2026-11-05" ya tienen respaldo. Un número distinto, como "9:45", sigue rechazado. | `app/agent/grounding.py` |
| 2 | `VERIFICATION_TALK` cuenta solo los pedidos de verificación de identidad: verificar la cuenta o la identidad, y mandar, ingresar o pasar un código. "Revisá el email para verificarlo" y "el código de seguimiento" ya no cuentan. | `app/agent/loop.py` |
| 3 | El prompt `system_v5` pide consultar el pedido antes de modificarlo. Ante una regla del negocio, pide explicarla con amabilidad y ofrecer una alternativa, como que una persona lo revise si el cliente quiere, en lugar de derivar. Va igual en las instrucciones de `cambiar_direccion`. | `prompts/system_v5.txt`, `case_types/cambiar_direccion.yaml` |
| 4 | Una respuesta vacía, con JSON, con metadatos ("Fuentes:", "factual:"), con marcas del modelo o con nombres de herramientas nunca llega al cliente. Si el texto suelto es el JSON de `responder` con un mensaje válido, se usa ese mensaje. Si no, hay un reintento y después el caso deriva. El panel lo muestra como "Descartó una respuesta que no se podía mostrar". | `app/agent/presentable.py`, `loop.py` |
| 5 | Los evals tienen un control nuevo, `presentable`: una respuesta así no cuenta como resuelta. La caché de respuestas ya no guarda los rechazos. | `evals/runner.py`, `app/llm/cache.py` |

### Medición con Groq real

Los 14 casos guionados, sin caché, dos veces por modelo, con 60 s de pausa entre casos para no chocar con el límite de tokens por minuto. El clasificador quedó en `gpt-oss-20b` y el juez en `qwen/qwen3.8-27b`. No se tocaron Render ni la línea base.

| Modelo del agente | Corrida | Resolución | Presentables | Juez | Fallan | Latencia por mensaje (mediana / p90 / máx.) | Sobre el umbral |
|---|---|---|---|---|---|---|---|
| `gpt-oss-20b` | 1 | 100 % | 14 de 14 | 0,81 | — | 3,5 / 8,3 / 39,0 s | 2 de 16 |
| `gpt-oss-20b` | 2 | 86 % | 12 de 14 | 0,64 | 04, 12 | 3,0 / 35,7 / 39,4 s | 3 de 16 |
| `gpt-oss-120b` | 1 | 86 % | 12 de 14 | 0,73 | 02, 04 | 4,0 / 37,9 / 49,3 s | 5 de 16 |
| `gpt-oss-120b` | 2 | 86 % | 12 de 14 | 0,66 | 02, 04 | 3,2 / 47,2 / 48,7 s | 4 de 16 |
| `qwen/qwen3.8-27b` | 1 | 93 % | 13 de 14 | 0,65 | 07 (falla crítica) | 3,8 / 6,2 / 44,1 s | 2 de 16 |
| `qwen/qwen3.8-27b` | 2 | 100 % | 14 de 14 | 0,66 | — | 3,4 / 11,8 / 48,3 s | 2 de 16 |

- **Presentables:** casos resueltos con una respuesta que se le puede mostrar al cliente. En las seis corridas los 14 mensajes finales fueron presentables: no llegó ningún JSON ni metadato. Antes de los arreglos, 120b mandaba JSON en 4 de cada 12 casos resueltos.
- **Antes y después con `gpt-oss-20b`:** antes de los arreglos dio 11 y 12 de 14, con la mayoría de las respuestas sacadas de la caché. Ahora da 14 y 12 de 14 sin caché.
- **Latencia:** la mediana bajó de unos 17 s a 3–4 s con las pausas. Los picos de 35 a 49 s siguen siendo esperas de Groq.

**Por qué fallan los que fallan:**
- **`gpt-oss-20b`, corrida 2, casos 04 y 12:** el modelo consulta el pedido y después elige derivar ("cambiar_direccion", "solicitud de cambio de dirección"). Ningún control lo frenó: es decisión del modelo, contra lo que pide el prompt. En la corrida 1 los dos casos salieron bien.
- **`gpt-oss-120b`, casos 02 y 04, en las dos corridas:** Groq no puede interpretar su llamada a herramienta tres veces seguidas ("Failed to parse tool call", "output_parse_failed") y el agente deriva. Es estructural, no depende del texto.
- **`qwen3.8-27b`, corrida 1, caso 07** ("¿Hacen arreglos de ruedo y costura?"): responde que no tiene registro de ese servicio y ofrece consultarlo con una persona, en lugar de derivar. El texto no inventa nada, pero la regla de los evals lo cuenta como falla crítica porque la respuesta correcta es derivar. Con esta configuración la compuerta del CI lo bloquearía.

**Notas:**
- El juez qwen sigue variando mucho: la misma derivación fija de 09 recibió de 0,2 a 1,0. Para comparar modelos sirven más la resolución y los presentables.
- Con qwen como agente, el juez es el mismo modelo y comparte su límite por minuto.

### Recomendación: seguir con `gpt-oss-20b`

- **Es la configuración que ya está en Render.** Con los arreglos pasó de 11 a 14 de 14 en su mejor corrida, y sus fallas son derivaciones: el cliente queda con una persona, que es la falla segura según la spec.
- **`gpt-oss-120b` queda descartado:** en las dos corridas falló en los mismos casos porque Groq no interpreta sus llamadas a herramientas.
- **`qwen3.8-27b` sacó un caso más (27 contra 26 de 28), pero no alcanza para cambiar:**
  - con dos corridas, un caso de diferencia está dentro de la variación;
  - su única falla es la más riesgosa: responder cuando había que derivar, que el CI trata como crítica;
  - cambiarlo obliga a cambiar también el juez de los evals, que hoy es qwen y tiene que ser un modelo distinto del agente (decisión 020).
- **Si se quiere evaluar a qwen en serio:** correr las 60 conversaciones de calidad con qwen y con 20b, sin caché, y decidir con esa muestra.
