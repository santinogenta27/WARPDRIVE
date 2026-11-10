-- 0012 — Pipeline de autoconfiguración (Hito 4).

-- Ingesta idempotente: el mismo archivo subido dos veces no se procesa de nuevo.
alter table sources add column content_hash text;
create unique index sources_content_idx on sources (tenant_id, content_hash)
  where content_hash is not null;

-- Descubrimiento de casos: resumen de cada conversación (intención, resultado, acción en un
-- sistema) y el tipo de caso al que quedó asignada.
alter table conversations add column summary jsonb;
alter table conversations add column case_type_id uuid;
alter table conversations add constraint conversations_case_type_fk
  foreign key (tenant_id, case_type_id) references case_types (tenant_id, id);

-- Avance de cada corrida del pipeline: el resultado de cada etapa, para retomar desde la
-- primera que falte, y el reporte de preparación.
create table onboarding_runs (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  status      text not null default 'running' check (status in ('running', 'done', 'failed')),
  stages      jsonb not null default '{}',
  report      jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (tenant_id, id)
);

alter table onboarding_runs enable row level security;
alter table onboarding_runs force row level security;
create policy tenant_isolation on onboarding_runs
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on onboarding_runs to app_user;
