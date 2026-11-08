# 008 — Guardián de políticas y aprobaciones

**Estado:** adoptada durante la corrida sin supervisión (opción más simple). Revisar.

## Decisiones

- **Orden de controles** (todos en código): herramienta habilitada para el tipo de caso y aprobada → esquema → contacto verificado → dueño del recurso → monto y riesgo → ejecución.
- **Montos:** sobre `max_amount` se rechaza siempre, aun con aprobación humana. Riesgo `high`, o monto sobre `approval_over`, crea una aprobación y no ejecuta nada. Riesgo `medium` con monto bajo el límite y riesgo `low` se ejecutan.
- **El monto** sale de `input.amount` o, si el manifiesto lo indica (`amount_from: order.total`), del recurso que trajo el control de dueño (cancelar un pedido de $152.000 pide aprobación).
- **Aprobación idéntica pendiente** (mismo caso, herramienta y parámetros) se reusa: el modelo no puede llenar la cola repitiendo el pedido.
- **Al decidir** (`app/policy/approvals.decide`): la fila se bloquea (`FOR UPDATE`), así dos decisiones simultáneas no ejecutan dos veces. Una aprobación ya decidida no se vuelve a ejecutar. La ejecución aprobada **vuelve a pasar por el guardián** (salvo la aprobación): si la herramienta se desaprobó, el pedido cambió de dueño o el monto supera el máximo, no se ejecuta. La clave de idempotencia (`case_id + order_id`) protege contra un reintento en la API del cliente.
- **Caso:** queda `waiting_approval` mientras hay una aprobación pendiente y vuelve a `open` al decidir.

## Pendiente

- La ruta `POST /approvals/{id}/decision` necesita la autenticación del panel (magic link, hito 5); la lógica ya está en `decide()`, que registra `decided_by`.
- Avisar al cliente el resultado (mensaje libre o plantilla `aprobacion_resuelta`, hito 3).
- Límites de monto configurables por tenant (pantalla de Configuración, hito 5).
