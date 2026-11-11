-- 0014 — Una sola aprobación pendiente por recurso (decisión 025).
--
-- `resource_key` identifica el recurso sobre el que actúa la herramienta (por ejemplo
-- `order_id=1005`), sin el caso. El índice único parcial garantiza en la base que el mismo
-- pedido no junte dos reembolsos pendientes, aunque lleguen dos conversaciones a la vez.
-- Las aprobaciones anteriores quedan con la clave en null y no participan del índice.
alter table approvals add column resource_key text;
create unique index approvals_pendiente_por_recurso_idx
  on approvals (tenant_id, tool_id, resource_key)
  where status = 'pending' and resource_key is not null;
