-- 0019 — Borrado de una empresa y auditoría del operador (hito 7, decisión 034).

-- Lo que hace el operador de la plataforma: crear y borrar empresas. No es una tabla de una
-- empresa (no lleva tenant_id ni RLS): la empresa borrada ya no existe, y su id queda acá
-- como dato. La aplicación (app_user) no la puede leer ni escribir; solo la conexión dueña.
create table operator_audit (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz not null default now(),
  operator     text not null,          -- email del operador
  action       text not null,          -- company_created, company_deleted
  company_id   uuid not null,
  company_name text not null,
  detail       jsonb not null default '{}'   -- solo cantidades, nunca datos de la empresa
);
create index operator_audit_recientes_idx on operator_audit (created_at desc);
revoke all on operator_audit from app_user;

create function operator_audit_insert_only() returns trigger
language plpgsql as $$
begin
  raise exception 'operator_audit es solo inserción' using errcode = 'insufficient_privilege';
end
$$;
create trigger operator_audit_no_update_delete
  before update or delete on operator_audit
  for each statement execute function operator_audit_insert_only();

-- audit_events sigue siendo de solo inserción. La única excepción es borrar la empresa entera:
-- el borrado marca `app.deleting_tenant` con su id, y solo se pueden borrar eventos de ese
-- tenant. Un UPDATE, o un DELETE sin esa marca o de otro tenant, falla como antes.
create or replace function audit_events_insert_only() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' and coalesce(current_setting('app.deleting_tenant', true), '') <> '' then
    return null;  -- el control fila por fila está en audit_events_delete_guard
  end if;
  raise exception 'audit_events es solo inserción' using errcode = 'insufficient_privilege';
end
$$;

create function audit_events_delete_guard() returns trigger
language plpgsql as $$
begin
  if old.tenant_id::text = current_setting('app.deleting_tenant', true) then
    return old;
  end if;
  raise exception 'audit_events es solo inserción' using errcode = 'insufficient_privilege';
end
$$;
create trigger audit_events_delete_only_whole_tenant
  before delete on audit_events
  for each row execute function audit_events_delete_guard();
