-- 0028 — Revisión de conversaciones (Hito 11, decisión 073). El equipo marca cada respuesta del
-- asistente como correcta o incorrecta; una incorrecta se puede convertir en un caso de eval.

create table reply_reviews (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  case_id      uuid not null,
  message_id   uuid not null references case_messages (id),
  verdict      text not null check (verdict in ('correct', 'incorrect')),
  reason       text check (reason in ('falta_informacion', 'informacion_incorrecta',
                 'accion_equivocada', 'debio_derivar', 'tono', 'otro')),
  comment      text not null default '',
  reviewed_by  uuid references users (id),
  eval_case_id uuid references eval_cases (id),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (tenant_id, message_id),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);
create index reply_reviews_open_idx on reply_reviews (tenant_id, updated_at)
  where verdict = 'incorrect' and eval_case_id is null;

alter table reply_reviews enable row level security;
alter table reply_reviews force row level security;
create policy tenant_isolation on reply_reviews
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on reply_reviews to app_user;
