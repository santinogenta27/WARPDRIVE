-- 0032 — Asistente para el equipo humano (Hito 12, decisión 082). La última sugerencia de cada
-- caso derivado o con borrador: resumen, paso siguiente y respuesta sugerida. `messages` es la
-- cantidad de mensajes del caso cuando se generó: si llegan más, se vuelve a generar.

create table case_assists (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  case_id    uuid not null,
  messages   integer not null,
  summary    text not null default '',
  next_step  text not null default '',
  reply      text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, case_id),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);

alter table case_assists enable row level security;
alter table case_assists force row level security;
create policy tenant_isolation on case_assists
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update, delete on case_assists to app_user;
