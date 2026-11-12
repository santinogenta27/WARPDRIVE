# 028 — Observabilidad: métricas, trazas y alertas

**Estado:** adoptada (10 de noviembre de 2026). Hito 6, etapa 3.

## Contexto

La spec pide una traza por caso con cada llamada al modelo, búsqueda y herramienta; alertas por errores, latencia y costo; y una pantalla de métricas. Hasta acá había auditoría por caso y costo por llamada, pero nada agregado ni exportable.

## Decisiones

### 1. Página Métricas (`/panel/metricas`)

Muestra los últimos 30 días de la empresa de la sesión. Todo sale de lo que el sistema ya registra:

| Bloque | De dónde sale |
|---|---|
| Resolución y derivación | `cases` y los eventos `escalated` de la auditoría |
| Conteo para el cobro | `billing_summary` (decisión 027) |
| Latencia p50 y p95, y promedio por paso | Eventos `turn_timing` de la auditoría (decisión 025) |
| Respaldos, rechazos y llamadas recuperadas | `usage.events` |
| Herramientas que fallaron | Eventos `tool_call` con estado `failed` |
| Respuestas cortadas por un error | `error_events` |
| Tokens y costo por día, por modelo y por caso | `usage` |
| Historial de evals | `eval_runs` |

- **Resolución:** de los casos ya resueltos, los que cerró el asistente sin una persona.
- **Derivación:** de todos los casos, los que pasaron a una persona.
- **CSAT no está.** La spec lo nombra, pero todavía no se le pregunta al cliente.
- Es HTML del servidor, como el resto del panel: tablas y números, sin gráficos.

### 2. Lo que faltaba registrar (migración 0016)

- **`usage.events`:** qué pasó al conseguir cada respuesta del modelo.
  - `respaldo`: respondió el proveedor de respaldo porque el principal no tenía cuota.
  - `recuperada`: llamada con el nombre de la herramienta roto, reparada (decisión 027).
  - `rechazo`: el modelo no dio una salida válida.
- **`error_events`:** errores que cortan una respuesta del chat web. El turno se revierte entero, así que el error se guarda aparte, en su propia transacción. Guarda el tipo de error, nunca el texto del cliente.
- **`eval_runs`:** modo, versión del prompt y modelo de cada corrida.

### 3. Historial de evals

- `make evals` con un modelo real guarda la corrida en `eval_runs` de la empresa de demo: resolución, juez, fallas críticas, versión del prompt y modelo.
- La corrida simulada no se guarda, salvo con `--record`: no mide un modelo.
- En una base sin empresa de demo (el CI) no guarda nada.
- Los evals corren en una transacción que se revierte, así que el historial usa una conexión propia.

### 4. OpenTelemetry: una traza por caso (`app/obs/tracing.py`)

- **El id de la traza es el id del caso.** Los dos son de 128 bits. Todos los turnos de un caso quedan en la misma traza, aunque pasen días. Con el id del caso se la encuentra en cualquier visor; la página Métricas lo muestra.
- **Spans:** un `agent.turn` por turno, con un hijo por llamada al modelo, por búsqueda y por pedido HTTP de una herramienta.
- **Atributos:** modelo pedido y modelo que respondió, tokens, costo, motivo de corte, eventos del proveedor, método y ruta de la herramienta, acción final, tipo de caso, cantidad de consultas a la base.
- **Decisiones del guardián:** van como eventos del turno, porque una herramienta puede hacer dos pedidos HTTP o ninguno.
- **Sin datos personales.** Ningún span lleva el texto del cliente ni parámetros de herramientas. Lo prueba `test_observabilidad.py`.
- **Los spans se arman al terminar el turno**, con los tiempos que ya mide `timing.py`. El agente no cambia.
- **Sin exportador configurado no hace nada.** Un problema al exportar nunca corta una respuesta.
- **Configuración**, con las variables estándar:
  - `OTEL_EXPORTER_OTLP_ENDPOINT`: envía por OTLP/HTTP a cualquier backend compatible.
  - `OTEL_TRACES_CONSOLE=1`: escribe las trazas en los logs.
- Las consultas a la base no son spans: son decenas por turno. Van como cantidad y tiempo en el turno.

### 5. Alertas en los logs (`app/obs/alerts.py`)

Una línea `ALERTA <tipo> clave=valor…` de nivel WARNING. Cualquier recolector de logs puede avisar filtrando por `ALERTA`.

| Alerta | Umbral por defecto | Variable |
|---|---|---|
| `latencia` | 10 s sin herramientas, 20 s con herramientas (spec) | `ALERT_LATENCY_MS`, `ALERT_LATENCY_TOOLS_MS` |
| `costo_por_caso` | USD 0,50 (spec) | `ALERT_COST_PER_CASE_USD` |
| `errores` | 5 errores de una empresa en 10 minutos | `ALERT_ERRORS`, `ALERT_ERRORS_WINDOW_S` |

- La de latencia dice qué paso fue el más lento.
- La de costo avisa una sola vez, cuando el caso cruza el umbral.
- La de errores avisa al llegar al umbral y en cada múltiplo. El contador vive en memoria: alcanza para un solo proceso.

### 6. Dependencias nuevas

- `opentelemetry-api`, `opentelemetry-sdk` y `opentelemetry-exporter-otlp-proto-http`, versión 1.45.2.
- **Por qué:** la spec define OpenTelemetry como la capa de observabilidad. El SDK arma y exporta los spans; el exportador OTLP/HTTP es el formato que aceptan los backends de trazas.
- `opentelemetry-api` ya estaba instalada como dependencia de `fastapi` y `anthropic`.

## Pendiente

- ~~CSAT.~~ Hecho en la decisión 033.
- Alerta por caída de la tasa de resolución (spec).
- Errores del worker de WhatsApp en `error_events`: hoy solo se registran los del chat web.
- ~~Un destino para las alertas.~~ Se envían por email al operador (decisión 033).
- Gráficos en la página Métricas.
