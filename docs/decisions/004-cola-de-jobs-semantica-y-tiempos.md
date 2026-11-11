# 004 — Cola de jobs: semántica de ejecución y tiempos

**Estado:** adoptada durante la corrida sin supervisión del hito 1 (opción más simple). Revisar.

## Contexto

La spec fija la cola en Postgres con `SELECT … FOR UPDATE SKIP LOCKED` y pide que los jobs sean idempotentes ("ejecutarlos dos veces no puede duplicar efectos"). No define reintentos, esperas ni qué pasa si un worker se cae.

## Decisiones

- **Tomar y ejecutar en dos transacciones.** `claim()` marca el job `running`, suma un intento y confirma. La ejecución abre otra transacción, bloquea la fila del job, corre el handler y marca `done` en esa misma transacción.
- **Efectos en la base: exactamente una vez.** Si el handler falla, su transacción se revierte entera (efectos incluidos) y el job vuelve a `queued` con espera. Si el proceso se cae, nada del handler queda confirmado.
- **Efectos externos** (llamar a la API de un cliente, enviar un WhatsApp): la cola no puede garantizarlos; cada handler usa una clave de idempotencia (el manifiesto de herramientas ya la define: `idempotency_key`).
- **Worker caído:** un job `running` con `locked_at` de hace más de `lock_timeout` (5 min por defecto) lo recupera otro worker, solo si le quedan intentos. Mientras un worker ejecuta, la fila está bloqueada y nadie más la toma.
- **Reintentos:** 5 intentos por defecto (`max_attempts`), espera exponencial `2^intento` segundos con tope de 5 minutos. Agotados los intentos, el job queda `failed` con su último error.
- **Encolar es idempotente** con `idempotency_key` (única): encolar dos veces con la misma clave devuelve el mismo job.

## Pendiente

Todavía no hay handlers registrados ni un proceso `worker` desplegable: llegan con las etapas del onboarding (hito 4) y el procesamiento de mensajes (hito 2/3).
