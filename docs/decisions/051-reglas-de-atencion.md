# 051 — Reglas de atención en el panel

**Contexto.** El Hito 9 pide configurar desde el panel, sin código: qué herramientas puede usar el asistente, montos máximos, qué requiere aprobación humana, si exige verificar identidad, horario de atención y a quién derivar. Las aplica el guardián, nunca el prompt.

**Qué ya estaba.** Aprobar o quitar una herramienta, el monto máximo y el monto sobre el que pide aprobación (decisión 026), todos aplicados por el guardián.

**Qué se decidió.**

- **Por herramienta**, dos casillas nuevas:
  - **"Exige identidad verificada"**: el guardián ya leía `requires_verified_contact` del manifiesto; ahora lo edita la empresa.
  - **"Siempre pide aprobación"**: pasa la herramienta a riesgo alto, que el guardián siempre manda a aprobación. Al sacarla, queda en riesgo medio y vuelve a valer el monto.
- **Horario de atención** del equipo (días y horas, hora de Buenos Aires), en `tenants.settings.rules`.
  - **El asistente atiende igual las 24 horas.** El horario es el de las personas: fuera de él, cuando el asistente deriva, el mensaje fijo suma cuándo responden ("Nuestro equipo atiende de lunes a viernes de 09:00 a 18:00").
  - No se cortó la atención fuera de horario: lo que el asistente resuelve solo no necesita a nadie.
- **A quién derivar:** un email por empresa. Recibe el aviso de cada caso derivado y de cada aprobación pendiente (la bandeja, decisión 054). Sin email, el aviso va a los usuarios de la empresa.
- Todo esto lo aplica el código (guardián y ciclo del agente). El prompt no cambia.
