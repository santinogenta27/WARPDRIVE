# 002 — Migraciones en SQL plano y rol de aplicación para RLS

**Estado:** adoptada durante la corrida sin supervisión del hito 1 (opción más simple). Revisar.

## Contexto

La spec pide esquema, migraciones y Row-Level Security, pero no define herramienta de migraciones ni cómo la aplicación se conecta para que la RLS aplique. En Neon, el usuario `neondb_owner` tiene `BYPASSRLS`: si la aplicación usara ese usuario directo, la RLS no filtraría nada.

## Opciones

1. SQLAlchemy + Alembic: ORM y migraciones autogeneradas. Dos dependencias grandes y una capa más entre el código y las políticas de RLS.
2. **SQL plano + runner propio** (`app/db/migrate.py`, ~40 líneas): archivos `NNNN_nombre.sql` aplicados en orden, una vez cada uno, registrados en `schema_migrations`, con lock advisory para que dos procesos no migren a la vez.

## Decisión

Opción 2. Además:

- **Rol `app_user`** (sin login ni BYPASSRLS), creado por la migración 0001. Toda consulta de negocio corre dentro de `tenant_scope(conn, tenant_id)`, que hace `SET LOCAL ROLE app_user` y fija `app.tenant_id` solo para la transacción. `SET LOCAL` es compatible con el pooler de Neon (modo transacción).
- **RLS forzada** en las 15 tablas de negocio y en `tenants`; sin tenant fijado, la sesión no ve ninguna fila.
- **Claves foráneas compuestas `(tenant_id, id)`**: la RLS no se aplica a las verificaciones de FK, así que sin esto un tenant podría referenciar una fila ajena conociendo su id.
- **`audit_events` solo inserción**: `app_user` no tiene UPDATE/DELETE y un trigger lo bloquea también para el dueño de la base. El borrado completo de un tenant (requisito de producción) deberá desactivar ese trigger de forma explícita.
- **Conexiones sin prepared statements** (`prepare_threshold=None`) por el pooler. Si diera problemas, usar la URL directa (sin `-pooler`).
- **Nombres de columnas en inglés**, consistentes con `tenant_id` y `case_type_id` de la spec. Equivalencias: `verificado_hasta` → `verified_until`, `ultima_entrada_cliente` → `last_customer_message_at`, `debe_escalar` → `must_escalate`, `resuelto_por` → `resolved_by`, `decidido_por` → `decided_by`.
- **Embeddings `vector(1024)`**: dimensión de los modelos multilingües de Voyage (decisión cerrada de la spec).
- La tabla `channels` (v1.1) no está: corresponde a la tarea 12e.
