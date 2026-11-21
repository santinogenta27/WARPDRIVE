-- 0029 — Prueba gratis self-service (decisión 075).

-- Una prueba gratis: es de la plataforma, como `signups` (sin RLS y sin acceso para app_user).
-- La empresa de prueba es un tenant común, con RLS. `company_id` (como en operator_audit) no es
-- clave foránea a propósito: la fila queda después de borrar la empresa, para el límite por
-- email y dominio y para el seguimiento del operador. Solo se guardan hashes del enlace y del
-- código.
create table trials (
  id                   uuid primary key default gen_random_uuid(),
  company_id           uuid,
  site_url             text not null,
  domain               text not null,
  email                text not null,
  language             text not null default 'es' check (language in ('es', 'en')),
  token_hash           text not null unique,
  code_hash            text,
  code_attempts        integer not null default 0,
  code_expires_at      timestamptz,
  ip_hash              text not null default '',
  status               text not null default 'code_sent'
                       check (status in ('code_sent', 'building', 'ready', 'failed', 'converted',
                                         'expired')),
  progress             jsonb not null default '{}',
  products             jsonb not null default '[]',
  messages             integer not null default 0,
  pilot_requested_at   timestamptz,
  contact_requested_at timestamptz,
  converted_at         timestamptz,
  created_at           timestamptz not null default now()
);
create unique index trials_email_key on trials (lower(email));
create unique index trials_domain_key on trials (domain);
create index trials_company_idx on trials (company_id);
create index trials_ip_idx on trials (ip_hash, created_at);
revoke all on trials from app_user;

-- El registro que viene de "Empezar mi piloto": al confirmar, convierte la empresa de prueba.
alter table signups add column trial_id uuid references trials (id);
