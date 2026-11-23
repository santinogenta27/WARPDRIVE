-- 0030 — Memoria del cliente (Hito 12, decisión 078). Un perfil por empresa y por cliente
-- verificado (`customer_key`: el hash del teléfono que demostró). Sin datos de pago.

create table customer_memories (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  customer_key text not null,
  name         text not null default '',
  preferences  jsonb not null default '[]',
  purchases    jsonb not null default '[]',
  history      jsonb not null default '[]',
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (tenant_id, customer_key),
  unique (tenant_id, id)
);
create index customer_memories_seen_idx on customer_memories (tenant_id, last_seen_at);

alter table customer_memories enable row level security;
alter table customer_memories force row level security;
create policy tenant_isolation on customer_memories
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update, delete on customer_memories to app_user;
