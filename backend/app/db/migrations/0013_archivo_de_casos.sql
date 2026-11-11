-- "Reiniciar demo" (decisión 024): los casos se archivan en lugar de borrarse. La auditoría es de
-- solo inserción, así que un caso con eventos no se puede borrar; archivado, sale del panel y del
-- agente y conserva su cadena de auditoría.
alter table cases add column archived_at timestamptz;
create index cases_activos_idx on cases (tenant_id, status) where archived_at is null;
