-- 0005 — Auditoría encadenada por caso: número de secuencia contiguo y fecha real de inserción.

alter table audit_events add column seq integer not null;
alter table audit_events alter column created_at set default clock_timestamp();
create unique index audit_events_case_seq_idx on audit_events (tenant_id, case_id, seq);
