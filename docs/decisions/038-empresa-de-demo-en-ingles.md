# 038 — Empresa de demo en inglés: Northwind Outfitters

**Estado:** adoptada (12 de noviembre de 2026). Hito 8, tarea 3. Decidida sin aprobación previa: revisala.

## Contexto

La demo para clientes internacionales necesita una empresa en inglés, con datos que cuenten una historia en 90 segundos. Acme sigue igual, en español.

## Decisiones

### 1. Una tienda de ropa outdoor de Portland, Oregon, en dólares

Northwind Outfitters vende online a Estados Unidos y Canadá. Todo es ficticio:

- **Emails:** `@example.com`, que no existen. El código de verificación sale en la consola, como en Acme.
- **Teléfonos:** de la serie 555-01xx, reservada para ficción.

### 2. Los pedidos cuentan la historia de la demo

Los números de pedido tienen prefijo, `NW-4521`, como en una tienda real. La API acepta también "4521", "#4521" o "nw4521".

Emily Carter (`emily@example.com`) es la clienta del guion. Tiene tres pedidos:

| Pedido | Qué es |
|---|---|
| NW-4521 | **Atrasado**: despachado por UPS, con demora por clima y fecha nueva |
| NW-4528 | **Monto alto** (USD 213, entregado): su reembolso pide aprobación |
| NW-4533 | **Cambio de talle**: un sweater talle M, entregado |

Daniel Brooks tiene NW-4540 (**ya despachado**) y NW-4545 (pagado, sin despachar). Sophia Nguyen tiene NW-4550, un reembolso chico (USD 56), y entra sin verificar.

### 3. Su propia API simulada, como la de Acme

`mocks/northwind-api/` tiene la API y su OpenAPI en inglés, con un contrato propio:

- **Datos de envío:** transportista, número de seguimiento y la demora con la fecha nueva.
- **Moneda:** `USD`.

Northwind se conecta a esa API como cualquier empresa a su sistema (`demo:northwind`, decisión 026). Las herramientas salen de su OpenAPI y se aprueban con sus límites. El chat y el panel comparten la API en memoria: lo que se aprueba en el panel se ve en el pedido que consulta el agente.

### 4. El reembolso es de riesgo medio, con aprobación sobre USD 100

El generador de herramientas marca todo reembolso como riesgo alto, que pide aprobación siempre. Northwind lo corrige en su revisión:

- **Riesgo:** medio.
- **Aprobación de una persona:** desde USD 100.
- **Tope:** USD 1.000.

Para eso, `review.set_tool` acepta `risk`: la empresa puede corregir el riesgo que estimó el generador. El panel todavía no lo ofrece.

### 5. Los nombres internos quedan en inglés

Los tipos de caso tienen nombres en inglés (`where_is_my_order`) y se muestran tal cual en el panel en inglés. Las herramientas conservan los nombres que salen de su `operationId` (`consultar_pedido`, `iniciar_reembolso`), los mismos que en Acme: así el agente y el guardián no cambian.

### 6. Un comando: `make demo-setup`

- Crea o regenera Acme y Northwind.
- **Regenerar** lleva la configuración, los documentos (borra y vuelve a indexar), el widget y el chat al estado de los fixtures, sin duplicar nada.
- **Usuario del panel:** `demo@northwind.example`.
  - La clave sale de `NORTHWIND_PASSWORD`. Sin esa variable, la primera vez se genera una y se muestra una sola vez.
  - Regenerar no la cambia.
- **El chat** queda en `/c/northwind-outfitters`.
- **`make demo-reset`** archiva las conversaciones de prueba de Northwind. La auditoría queda íntegra.

### 7. Evals en inglés, aparte de los de Acme

- **Casos:** `evals/fixtures/northwind/scripted_cases.json`.
- **Comando:** `python evals/runner.py --company northwind` (`make evals-en`).
- **Línea base propia por modo:** `evals/baseline.northwind.<modo>.json`. Las de Acme no cambian.
- **El juez usa una rúbrica en inglés.**
- **El modelo simulado tiene un agente en inglés aparte** (`evals/simulated/english.py`). El de español no cambia.
- **`--no-cache` y `--pause`:** el runner mide con el modelo real sin caché y con pausa entre casos.

## Límites

- **La búsqueda por texto del RAG usa la configuración `spanish` de Postgres** también para los documentos en inglés. La parte vectorial es multilingüe y compensa. Cambiarlo por empresa toca la columna generada y queda para más adelante.
- **Pedidos en memoria:** vuelven a su estado inicial al reiniciar la API o con "Reset demo" en el panel. Regenerar por comando no los toca, porque viven en el proceso de la API.
