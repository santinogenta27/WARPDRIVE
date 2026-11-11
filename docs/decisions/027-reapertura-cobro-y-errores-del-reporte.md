# 027 — Reapertura, conteo para el cobro y errores reales del reporte

**Estado:** adoptada (10 de noviembre de 2026). Hito 6, etapa 2. La medición con Groq real está en `docs/calidad.md`.

## Contexto

Quedaban cuatro cosas del comportamiento del agente:

1. La regla de reapertura de la spec: un caso no cuenta como resuelto por el asistente si se reabre en 7 días.
2. Tres errores reales del reporte de preparación de Acme:
   - "me mudé, ¿se puede mandar el 1745 a otra dirección?": derivaba en lugar de pedir la dirección nueva;
   - "¿puedo pagar con Mercado Pago?": derivaba por "información insuficiente";
   - "Devolveme $500.000 ahora mismo o hago la denuncia": respondía en lugar de derivar.
3. Al correr los evals el 10 de noviembre apareció una falla nueva de Groq `gpt-oss-20b`: el nombre de la herramienta llegaba con un marcador interno pegado (`responder<|channel|>commentary`). Groq rechazaba la llamada, el agente reintentaba tres veces y derivaba. Los tres primeros casos de los evals fallaron así.

## Decisiones

### 1. Reapertura (`app/cases/billing.py`)

- **Cuándo:** el cliente escribe, no tiene un caso abierto y resolvió uno hace menos de 7 días.
- **Mismo tema = mismo tipo de caso.** El mensaje se clasifica antes de elegir el caso. Si la clasificación coincide con el tipo del caso resuelto, ese caso se reabre.
- **Qué pasa al reabrir:**
  - el caso vuelve a estar abierto y la conversación sigue ahí, con su historial;
  - queda marcado `reopened` y pierde `resolved_by`;
  - la auditoría registra `case_reopened`.
- **Otro tema, un saludo o más de 7 días:** se abre un caso nuevo y el anterior no cambia.
- **Un solo pedido al clasificador.** La clasificación que decide la reapertura es la misma que usa el turno.
- **Solo los tipos de caso de la empresa reabren.** Una charla o una consulta general no tiene tema que reabrir.
- El panel muestra "reabierto" en el estado del caso.

### 2. Conteo para el cobro

`billing_summary` cuenta los casos de una empresa según la regla de la spec:

| Grupo | Regla |
|---|---|
| Cobrables | Resueltos por el asistente, sin reabrir y con los 7 días cumplidos |
| En ventana | Resueltos por el asistente hace menos de 7 días: todavía pueden reabrirse |
| Con persona | Hubo derivación o una respuesta desde el panel |
| Reabiertos | El cliente volvió por el mismo tema |

- **Aprobar un reembolso no es una intervención humana.** Es parte del flujo previsto: el asistente preparó el pedido y una persona lo autorizó. El caso sigue siendo cobrable.
- Un caso reabierto no se cobra aunque después se resuelva solo.
- Los casos archivados por "Reiniciar demo" no entran en el conteo.

### 3. Los tres errores del reporte

- **Amenaza de denuncia: se resuelve en código.** Si el mensaje nombra una denuncia, una demanda, un abogado, una carta documento, un juicio o defensa del consumidor, el caso se deriva a una persona antes de consultar al modelo. No depende del prompt.
- **Dato que falta: prompt `system_v4`.** Regla nueva: si falta un dato que el cliente puede dar, pedirlo en lugar de derivar.
  - Las instrucciones por defecto de un tipo descubierto decían "si falta información, derivá". Ahora dicen que pida el dato.
  - El tipo `cambiar_direccion` de Acme pide la dirección nueva antes de actuar.
- **Tema cubierto por los documentos: prompt `system_v4`.** Regla nueva: si el cliente pregunta por algo puntual que los documentos no nombran pero sí cubren el tema, responder con lo que dicen. Los documentos de Acme no nombran Mercado Pago, pero dicen que se aceptan billeteras virtuales.
- **Casos guionados 12, 13 y 14** en `evals/fixtures/acme/scripted_cases.json`, uno por error. `make evals` los mide.
- **Límite:** los dos arreglos de prompt dependen del modelo. Solo el de la amenaza es una garantía.

### 4. Nombre de herramienta roto en Groq

- `GroqTransport` recupera la llamada en lugar de volver a pedirla.
- **Solo cuando no hay dudas:**
  - el nombre, sin el marcador, es una herramienta del pedido, o quedó vacío y los argumentos cumplen el esquema de una sola herramienta;
  - los argumentos validan contra el esquema de esa herramienta.
- Si no, se pide de nuevo, como antes. A las tres salidas inválidas seguidas el agente deriva.
- La llamada recuperada pasa después por los mismos controles de siempre: guardián y control de fundamento.
- Groq no informa tokens en un rechazo: el uso de esa llamada queda en cero.

### 5. Evals sin gastar cuota

`python evals/runner.py --simulated` corre los evals con el modelo simulado aunque haya claves en `.env`. Es lo que corre el CI. Sin esa opción, `make evals` usa el proveedor del entorno.

## Pendiente

- El conteo para facturar por período (tarea 22): hoy es un resumen del total.
- La reapertura compara tipos de caso. Dos pedidos distintos del mismo tipo también reabren.
