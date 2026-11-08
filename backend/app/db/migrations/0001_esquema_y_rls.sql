-- 0001 — Esquema base, aislamiento por tenant (RLS) y rol de aplicación.
--
-- Reglas:
--   * Toda tabla de negocio lleva tenant_id NOT NULL, RLS habilitada y forzada, y una política
--     que la limita al tenant de la sesión (app.tenant_id, fijado con SET LOCAL).
--   * Las claves foráneas entre tablas de negocio son compuestas (tenant_id, id): una fila no
--     puede apuntar a otra de un tenant distinto aunque se conozca su id.
--   * La aplicación opera como app_user (sin BYPASSRLS). jobs no es tabla de negocio.

create extension if not exists vector;

-- Rol de aplicación ---------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_user') then
    create role app_user nologin;
  end if;
  execute format('grant app_user to %I', current_user);
end
$$;

create or replace function app_current_tenant() returns uuid
language sql stable as $$
  select nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

-- Tenants y usuarios ---------------------------------------------------------------------------
create table tenants (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  plan       text not null default 'trial',
  limits     jsonb not null default '{}',
  status     text not null default 'active' check (status in ('active', 'suspended', 'deleted')),
  created_at timestamptz not null default now()
);

create table users (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  email      text not null,
  role       text not null check (role in ('owner', 'agent', 'viewer')),
  created_at timestamptz not null default now(),
  unique (tenant_id, email),
  unique (tenant_id, id)
);

-- Onboarding ----------------------------------------------------------------------------------
create table sources (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  kind       text not null check (kind in ('whatsapp_export', 'helpdesk_export', 'document', 'url')),
  file_path  text,
  status     text not null default 'pending',
  created_at timestamptz not null default now(),
  unique (tenant_id, id)
);

create table conversations (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  source_id   uuid,
  external_id text,
  started_at  timestamptz,
  created_at  timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, source_id) references sources (tenant_id, id)
);

create table messages (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid not null references tenants (id),
  conversation_id uuid not null,
  role            text not null check (role in ('customer', 'agent')),
  text            text not null, -- siempre anonimizado
  sent_at         timestamptz,
  unique (tenant_id, id),
  foreign key (tenant_id, conversation_id) references conversations (tenant_id, id)
);

create table case_types (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  name         text not null,
  volume       integer not null default 0,
  examples     jsonb not null default '[]',
  active       boolean not null default false,
  instructions text, -- YAML del tipo de caso
  created_at   timestamptz not null default now(),
  unique (tenant_id, name),
  unique (tenant_id, id)
);

-- RAG -----------------------------------------------------------------------------------------
create table documents (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  title      text not null,
  source     text,
  created_at timestamptz not null default now(),
  unique (tenant_id, id)
);

create table chunks (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  document_id uuid not null,
  text        text not null,
  embedding   vector(1024),
  tsv         tsvector generated always as (to_tsvector('spanish'::regconfig, text)) stored,
  source      text,
  unique (tenant_id, id),
  foreign key (tenant_id, document_id) references documents (tenant_id, id)
);
create index chunks_embedding_idx on chunks using hnsw (embedding vector_cosine_ops);
create index chunks_tsv_idx on chunks using gin (tsv);

-- Herramientas y evals ------------------------------------------------------------------------
create table tools (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  name         text not null,
  input_schema jsonb not null,
  manifest     jsonb not null, -- manifiesto completo (contrato de la spec)
  risk         text not null check (risk in ('low', 'medium', 'high')),
  max_amount   numeric(14, 2),
  approved     boolean not null default false,
  created_at   timestamptz not null default now(),
  unique (tenant_id, name),
  unique (tenant_id, id)
);

create table eval_cases (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants (id),
  case_type_id  uuid,
  input         jsonb not null,
  expected      jsonb not null,
  must_escalate boolean not null default false,
  origin        text not null, -- conversación fuente o 'adversarial'
  created_at    timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, case_type_id) references case_types (tenant_id, id)
);

create table eval_runs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants (id),
  agent_version text not null,
  results       jsonb not null default '[]',
  scores        jsonb not null default '{}',
  created_at    timestamptz not null default now(),
  unique (tenant_id, id)
);

-- Producción ----------------------------------------------------------------------------------
create table contacts (
  id                       uuid primary key default gen_random_uuid(),
  tenant_id                uuid not null references tenants (id),
  phone_hash               text not null,
  verified_until           timestamptz,
  last_customer_message_at timestamptz,
  created_at               timestamptz not null default now(),
  unique (tenant_id, phone_hash),
  unique (tenant_id, id)
);

create table cases (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  contact_id   uuid not null,
  case_type_id uuid,
  status       text not null default 'open'
               check (status in ('open', 'waiting_approval', 'escalated', 'resolved')),
  resolved_by  text check (resolved_by in ('agent', 'human')),
  reopened     boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  closed_at    timestamptz,
  unique (tenant_id, id),
  foreign key (tenant_id, contact_id) references contacts (tenant_id, id),
  foreign key (tenant_id, case_type_id) references case_types (tenant_id, id)
);

create table approvals (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  case_id     uuid not null,
  tool_id     uuid not null,
  params      jsonb not null,
  status      text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  decided_by  uuid,
  decided_at  timestamptz,
  created_at  timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, case_id) references cases (tenant_id, id),
  foreign key (tenant_id, tool_id) references tools (tenant_id, id),
  foreign key (tenant_id, decided_by) references users (tenant_id, id)
);

create table audit_events (
  id         uuid primary key default gen_random_uuid(),
  tenant_id  uuid not null references tenants (id),
  case_id    uuid,
  kind       text not null,
  payload    jsonb not null,
  prev_hash  text,          -- hash del evento anterior (cadena, tarea 10)
  hash       text not null,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);

create table usage (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references tenants (id),
  case_id       uuid,
  model         text not null,
  input_tokens  integer not null default 0,
  output_tokens integer not null default 0,
  cost_usd      numeric(12, 6) not null default 0,
  created_at    timestamptz not null default now(),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);

-- Cola de trabajos (no es tabla de negocio: la usa el worker del sistema) ---------------------
create table jobs (
  id              uuid primary key default gen_random_uuid(),
  kind            text not null,
  payload         jsonb not null default '{}',
  status          text not null default 'queued'
                  check (status in ('queued', 'running', 'done', 'failed')),
  attempts        integer not null default 0,
  max_attempts    integer not null default 5,
  run_after       timestamptz not null default now(),
  locked_at       timestamptz,
  last_error      text,
  idempotency_key text unique,
  created_at      timestamptz not null default now(),
  finished_at     timestamptz
);
create index jobs_ready_idx on jobs (run_after) where status = 'queued';

-- audit_events: solo inserción, también para el dueño de la base ------------------------------
create or replace function audit_events_insert_only() returns trigger
language plpgsql as $$
begin
  raise exception 'audit_events es solo inserción' using errcode = 'insufficient_privilege';
end
$$;
create trigger audit_events_no_update_delete
  before update or delete on audit_events
  for each statement execute function audit_events_insert_only();

-- RLS por tenant ------------------------------------------------------------------------------
alter table tenants enable row level security;
alter table tenants force row level security;
create policy tenant_isolation on tenants
  using (id = app_current_tenant()) with check (id = app_current_tenant());

do $$
declare
  t text;
begin
  foreach t in array array[
    'users', 'sources', 'conversations', 'messages', 'case_types', 'documents', 'chunks',
    'tools', 'eval_cases', 'eval_runs', 'contacts', 'cases', 'approvals', 'audit_events', 'usage'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format(
      'create policy tenant_isolation on %I using (tenant_id = app_current_tenant()) '
      'with check (tenant_id = app_current_tenant())', t);
  end loop;
end
$$;

-- Permisos de app_user ------------------------------------------------------------------------
grant usage on schema public to app_user;
grant select, insert, update, delete on all tables in schema public to app_user;
revoke update, delete on audit_events from app_user;
revoke all on schema_migrations from app_user;
alter default privileges in schema public grant select, insert, update, delete on tables to app_user;
