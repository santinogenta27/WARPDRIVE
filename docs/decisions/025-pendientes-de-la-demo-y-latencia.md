# 025 — Pendientes de la demo: aprobación única, textos legibles y latencia

**Estado:** adoptada (10 de noviembre de 2026). Hito 6, etapa 0.

## Contexto

Después de la demo en Render quedaron tres pendientes:

1. El mismo pedido podía juntar dos reembolsos pendientes: uno por cada conversación del cliente, o uno por cada monto distinto.
2. La pantalla de Aprobaciones mostraba identificadores: `iniciar_reembolso`, `amount` y `order_id`.
3. Las respuestas tardaban y no se sabía en qué paso.

## Decisiones

### 1. Una sola aprobación pendiente por recurso

- **Qué es "el mismo pedido":** los campos de la clave de idempotencia del manifiesto, sin el caso. Para `"case_id + order_id"` es `order_id=1005`. Se guarda en `approvals.resource_key` (migración 0014).
- **Mismo caso:** se reusa la aprobación que ya está pendiente, aunque el modelo pida otro monto.
- **Otro caso:** el guardián no crea nada y devuelve `denied` con el motivo "ya hay una solicitud pendiente". El agente responde con un mensaje fijo: la solicitud ya está en revisión.
- **Garantía en la base:** un índice único parcial sobre `(tenant_id, tool_id, resource_key)` para las pendientes. Dos conversaciones simultáneas no pueden crear dos.
- **Después de decidir:** aprobada o rechazada, el pedido puede volver a pedirse.
- Las aprobaciones anteriores a la migración quedan sin clave y no participan del índice.
- Una herramienta sin clave de idempotencia se comporta como antes: se reusa la aprobación idéntica del mismo caso.

### 2. Textos legibles en Aprobaciones

- `action_label` (`app/panel/labels.py`) arma "Reembolso · Pedido 1005 · $35.000".
- Las cuatro herramientas de Acme tienen nombre propio. Para una herramienta generada en el onboarding, el nombre sale del identificador: `crear_nota_de_credito` se muestra como "Nota de crédito".
- El pedido va primero y el monto al final. jsonb no conserva el orden de las claves.
- La auditoría del caso sigue mostrando el nombre técnico de la herramienta y sus parámetros: es el registro exacto de lo que pasó.

### 3. Latencia: medir por paso y reducir donde más pesa

**Medición** (`app/obs/timing.py`):

- Cada turno del agente reparte su tiempo en cuatro pasos: base (consultas a Postgres), modelo, búsqueda y herramientas.
- El tiempo es exclusivo: se le cuenta al paso más interno, así la suma no supera el total.
- El resultado queda en la auditoría del caso como `turn_timing`, con los milisegundos por paso y la cantidad de llamadas. El panel lo muestra como "Tiempo de la respuesta".
- `python evals/latency.py` corre cuatro mensajes típicos y muestra la tabla. Con `--real` usa los modelos reales.

**Resultado, desde la PC contra Neon, con el modelo simulado:**

| Mensaje | Antes: base | Antes: viajes | Después: base | Después: viajes |
|---|---|---|---|---|
| "hola" | 3,9 s | 91 | 0,9 s | 21 |
| "¿Abren los domingos?" | 4,5 s | 115 | 1,0 s | 28 |
| "¿Dónde está mi pedido 1001?" | 5,6 s | 144 | 1,4 s | 35 |
| "Quiero el reembolso del pedido 1005" | 7,1 s | 176 | 1,8 s | 45 |

- **Dónde pesaba:** en la base. Cada consulta fijaba el rol y el tenant, corría y los restauraba: cinco viajes a Postgres por consulta. Cada viaje tarda unos 40 ms desde la PC.
- La búsqueda tardaba 0,5 s y bajó a 0,2 s por el mismo motivo. Las herramientas y el modelo simulado no pesan.
- El modelo real no entra en esta tabla. Su tiempo queda en `turn_timing` de cada caso de la demo.

**Qué se cambió:**

- **Un solo alcance de tenant por turno.** `handle_message` abre `tenant_scope` una vez y las consultas de adentro lo reusan. La RLS no cambia: todo el turno corre como `app_user` limitado al tenant.
- **`tenant_scope` en un viaje.** Rol y tenant se fijan con una sola consulta, y se restauran con otra.
- **Auditoría:** el bloqueo del caso y la lectura del último evento salen en una consulta. Son dos viajes por evento en lugar de tres.
- **Historial:** se lee una vez por turno en lugar de dos.
- `test_latencia.py` pone un tope de viajes por turno, para frenar una regresión.

## Pendiente

- El camino del chat web fuera del agente (sesión, límites, canal) suma unos viajes más. No se tocó.
- La auditoría todavía hace dos viajes por evento y un turno registra unos diez eventos. Se podría escribir en lote al final del turno.
- Medir en Render con Groq real: mirar "Tiempo de la respuesta" en la auditoría de un caso.
