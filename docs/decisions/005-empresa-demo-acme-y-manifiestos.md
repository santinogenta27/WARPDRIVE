# 005 — Empresa de demo Acme y manifiestos de herramientas

**Estado:** adoptada durante la corrida sin supervisión (opción más simple). Revisar.

## Contexto

La spec define Acme Store (8 tipos de caso, 4 herramientas con su riesgo, 10 casos guionados) y pide 500 conversaciones y 20 documentos generados una sola vez con Opus. No hay `ANTHROPIC_API_KEY` todavía.

## Decisiones

- **API simulada** (`mocks/acme-api/`): FastAPI con contrato escrito a mano en `openapi.json` (fuente de verdad). Cada `create_app()` arranca con datos limpios de `data/orders.json` (6 pedidos ficticios que cubren los casos guionados: despachado, no despachado, de otro cliente, sobre $100.000 y entregado). Cancelación y reembolso respetan `Idempotency-Key`.
- **Manifiestos** en `evals/fixtures/acme/tools/`, con el contrato de la spec. Dos agregados mínimos:
  - `amount_from`: la cancelación no recibe monto, así que el guardián evalúa `order.total` contra el límite de $100.000.
  - El cuerpo de la llamada upstream son los campos de entrada que no van en la ruta (por eso `actualizar_direccion` recibe `address`, como la API).
- **Tipos de caso** en YAML (`evals/fixtures/acme/case_types/`), con la estructura de la spec. Los 8 quedan activos en el fixture.
- **Contactos de demo**: dos verificados (Lucía, dueña de 1001/1002/1004; Martín, de 1003/1005) y una no verificada (Sofía, 1006).

## Pendiente (necesita clave de Anthropic)

- Script que genere con Opus las 500 conversaciones sintéticas (se usan en el hito 4: descubrimiento de casos). Mientras tanto, los documentos de políticas se escriben a mano en la tarea 6.
