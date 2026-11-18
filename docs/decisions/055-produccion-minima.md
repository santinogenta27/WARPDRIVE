# 055 — Producción mínima

**Contexto.** El Hito 9 pide plantillas de términos y privacidad (a revisar por un abogado), exportar y borrar los datos de una empresa, backups documentados, límites de uso, alertas de errores y cómo rotar todos los secretos en `docs/render.md`.

**Qué ya estaba.** Borrar una empresa (decisión 034); límites por sesión, IP y día (decisión 019); alertas por latencia, costo y errores, en logs y por email (decisiones 028 y 033); tope de gasto (decisión 049).

**Qué se decidió.**

- **Plantillas legales** en `docs/legal/` (términos y privacidad), con "BORRADOR" y los datos a completar entre corchetes. Base: Ley 25.326. No se publican en el sitio hasta que las revise un abogado.
- **Exportar una empresa:** Empresas → **Exportar** (operador). Un .zip con un JSON por tabla, con el mismo catálogo que el borrado: una tabla nueva entra sola. Sin secretos (credenciales y keys cifradas, claves del panel), sin embeddings ni el logo. Queda en `operator_audit`.
- **Rotar `ENCRYPTION_KEY` sin perder datos:** la variable acepta varias claves separadas por comas, la nueva primero (`MultiFernet`). Se cifra con la primera y se descifra con cualquiera. `python -m app.rotate_keys` vuelve a cifrar todo con la nueva; después se deja solo la nueva. Así no hay un momento en que algo cifrado no se pueda leer.
- **Backups:** el historial de Neon más un `pg_dump` semanal propio, con la restauración probada una vez por mes en una rama vacía de Neon. Gratis, a mano: sin presupuesto no hay un servicio de backups.
- **Rotación de cada secreto:** una tabla en `docs/render.md` con dónde se genera cada uno y qué pasa al cambiarlo.
