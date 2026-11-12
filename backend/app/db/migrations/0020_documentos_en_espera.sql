-- 0020 — Documentos que esperan confirmación (hito 7, decisión 035).

-- Un documento del onboarding que parece tener datos de varias personas no se indexa hasta que
-- la empresa lo confirma. Mientras tanto su texto queda acá, ya sin tarjetas ni DNI. Al
-- confirmar pasa al RAG; al descartar se borra. El archivo original nunca se conserva.
create table pending_documents (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references tenants (id),
  name         text not null,   -- nombre del archivo, como lo subió la empresa
  title        text not null,
  source       text not null,
  text         text not null,
  content_hash text not null,   -- del archivo original: subirlo de nuevo no lo duplica
  reason       text not null,   -- por qué espera: "4 emails y 4 teléfonos distintos"
  created_at   timestamptz not null default now(),
  unique (tenant_id, content_hash)
);

alter table pending_documents enable row level security;
alter table pending_documents force row level security;
create policy tenant_isolation on pending_documents
  using (tenant_id = app_current_tenant()) with check (tenant_id = app_current_tenant());
grant select, insert, delete on pending_documents to app_user;
