-- 0002 — Detalle de uso por llamada: tipo (llm o embedding) y tokens de caché de prompts.

alter table usage
  add column kind text not null default 'llm' check (kind in ('llm', 'embedding')),
  add column cache_creation_input_tokens integer not null default 0,
  add column cache_read_input_tokens integer not null default 0;

create index usage_tenant_case_idx on usage (tenant_id, case_id);
