-- 0009 — Verificación de identidad por código al email (spec v1.1, "Verificación de identidad").

-- Teléfono (hash) del cliente que el contacto demostró ser con el código. Los controles de dueño
-- del guardián lo usan en lugar del teléfono del contacto (en el chat web no hay teléfono).
alter table contacts add column verified_phone_hash text;

create table identity_verifications (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references tenants (id),
  contact_id  uuid not null,
  case_id     uuid not null,
  question    text not null, -- consulta que quedó pendiente; se responde al verificar
  status      text not null default 'awaiting_email'
              check (status in ('awaiting_email', 'code_sent', 'verified', 'failed')),
  code_hash   text,          -- el código nunca se guarda en claro
  phone_hash  text,          -- cliente del email; null si el email no está registrado
  attempts    integer not null default 0,
  expires_at  timestamptz,
  created_at  timestamptz not null default clock_timestamp(),
  foreign key (tenant_id, contact_id) references contacts (tenant_id, id),
  foreign key (tenant_id, case_id) references cases (tenant_id, id)
);
create index identity_verifications_open_idx on identity_verifications (tenant_id, contact_id)
  where status in ('awaiting_email', 'code_sent');

alter table identity_verifications enable row level security;
alter table identity_verifications force row level security;
create policy tenant_isolation on identity_verifications
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, update on identity_verifications to app_user;
