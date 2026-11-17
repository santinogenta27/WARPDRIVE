# 054 — Aviso al equipo por email

**Contexto.** El Hito 9 pide una bandeja para humanos: casos derivados y aprobaciones pendientes, con aviso por email y respuesta desde el panel. La bandeja y la respuesta ya estaban (Casos, con los derivados primero, y Aprobaciones; decisión 022).

**Qué se decidió.**

- **Cuándo:** cuando un turno termina derivado o pidiendo aprobación. Una sola vez por caso derivado y por aprobación (`team_notified` en la auditoría, con la cantidad de destinatarios y sin las direcciones).
- **A quién:** el email "a quién derivar" de las reglas (decisión 051); si no hay, los usuarios de la empresa con rol `owner` o `agent`.
- **Qué dice:** el motivo (el de la derivación o la acción a aprobar) y el enlace al caso o a Aprobaciones. **Nunca lo que escribió el cliente** ni sus datos: el email puede quedar en casillas compartidas.
- **El enlace** usa `PUBLIC_BASE_URL` (por ejemplo `https://enterprise-data-ai.onrender.com`); sin esa variable, la ruta sola.
- **Sin proveedor de email** (sin `RESEND_API_KEY`), el aviso queda en el log y en la auditoría. Sale en otro hilo: no demora la respuesta al cliente.
- **Las empresas de demo** (Acme y Northwind) no tienen usuarios propios ni email de derivación: no se envía nada, así la demo no cambia.
