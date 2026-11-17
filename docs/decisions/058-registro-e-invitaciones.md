# 058 — Registro self-service e invitaciones con roles

**Contexto.** El Hito 9 pide que una empresa nueva cree su cuenta sola: registro, verificación de email, primer usuario admin e invitación de compañeros con roles (admin y agente humano). Hasta ahora solo el operador creaba empresas (decisión 026).

**Qué se decidió.**

- **Registro en `/registro`:** nombre de la empresa, email y clave. Se envía un enlace al email; **recién al abrirlo** se crea la empresa, con ese usuario como admin, y entra al panel. El enlace vence a las 24 horas y sirve una vez.
  - Se guarda en `signups` sin `tenant_id` (la empresa todavía no existe), como `operator_audit`. Solo hashes del enlace y de la clave.
  - El registro pendiente vencido se borra solo en el registro siguiente.
- **Invitaciones:** Equipo → **Invitar**, con email y rol. Vence a los 7 días; el invitado elige su clave en el enlace.
- **Roles:**
  - **Admin** (`owner`): todo el panel de su empresa.
  - **Persona del equipo** (`agent`): Casos, Aprobaciones, Reporte y Métricas. No entra a Configuración, Onboarding ni Equipo (403).
  - El rol va en la sesión firmada; las sesiones viejas sin rol valen como admin, como antes.
- **El operador sigue pudiendo crear empresas** desde Empresas.
- **Sin email real** (sin Resend), el enlace sale en el log del servidor, como el código de verificación (decisión 029).
- **Abuso:** como el chat, tiene un límite: 5 registros por email por día.
