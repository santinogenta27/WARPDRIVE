-- 0011 — Uso del chat web para los límites diarios por IP y totales (demo pública: cuidar la
-- cuota del modelo). Una fila por mensaje respondido. La IP no se guarda: solo un hash con sal.

create table web_usage (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  ip_hash     text not null,
  created_at  timestamptz not null default clock_timestamp()
);
create index web_usage_recent_idx on web_usage (tenant_id, created_at);
create index web_usage_ip_idx on web_usage (tenant_id, ip_hash, created_at);

alter table web_usage enable row level security;
alter table web_usage force row level security;
create policy tenant_isolation on web_usage
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert on web_usage to app_user;
