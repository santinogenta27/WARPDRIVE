# 029 — Email real para el código de verificación (Resend)

**Estado:** adoptada (11 de noviembre de 2026).

## Contexto

La spec pide un "proveedor transaccional detrás de `Mailer`". Hasta acá solo había `ConsoleMailer`: el código de verificación salía en los logs del servidor y no llegaba a nadie (decisión 018).

## Decisiones

### 1. Proveedor: Resend, por su API HTTP

- `ResendMailer` (`app/mailer.py`) hace un `POST https://api.resend.com/emails` con `httpx`.
- **Sin dependencias nuevas.** `httpx` ya estaba. No se usa el SDK de Resend: es un solo pedido.
- El email va en texto plano: asunto y cuerpo, como los arma el agente.
- Espera hasta 10 segundos.

Opciones descartadas:

- **SMTP:** el plan gratis de Render bloquea los puertos de salida de SMTP.
- **SDK de Resend:** una dependencia más para un solo pedido HTTP.

### 2. Se activa con dos variables

- `RESEND_API_KEY` y `MAIL_FROM`. Hacen falta las dos.
- Sin ellas sigue `ConsoleMailer`: desarrollo, tests y CI no cambian.
- `MAIL_FROM` es el remitente, de un dominio verificado en Resend. Ejemplo: `Enterprise Data AI <codigos@enterprisedataai.com>`.
- Es un remitente para todas las empresas. Un remitente por empresa queda pendiente.

### 3. Si el envío falla

Antes no podía fallar. Ahora sí: Resend caído, clave vencida, dominio sin verificar o límite de envíos.

- El cliente ve un mensaje fijo: "No pudimos enviarte el código en este momento. Esperá un minuto y pasame de nuevo tu email, así te mando otro."
- **El código que no llegó deja de valer.** La verificación vuelve a esperar el email, con la consulta pendiente guardada.
- Cuando el cliente manda otra vez su email, se genera y se envía un código nuevo.
- El fallo queda en tres lugares:
  - auditoría del caso: evento `verification_code_failed`;
  - `error_events`, con tipo `MailError`: se ve en la página Métricas;
  - la alerta `errores` de los logs (decisión 028).
- **Sin datos personales.** El detalle del error lleva el código HTTP y el tipo de error de Resend. No lleva el destinatario, el código ni la clave. No se usa el `message` de Resend, que puede repetir la dirección.

### 4. Direcciones de prueba: no se envían

- Los clientes de la demo de Acme tienen emails `@example.com`. Esas direcciones no existen: enviarles rebota y baja la reputación del dominio.
- `ResendMailer` no envía a dominios reservados para pruebas (RFC 2606): `example.com`, `example.org`, `example.net`, y los que terminan en `.example`, `.test`, `.invalid` o `.localhost`.
- Para esas direcciones el código sale en los logs, como antes. **La demo de Acme sigue igual con Resend activado.**

### 5. El replay del onboarding nunca envía

El reporte de preparación hace pasar conversaciones del historial por el agente. Ese agente usa siempre `ConsoleMailer`: un email real del historial no puede recibir un código.

## Límite conocido

El mensaje de fallo solo aparece cuando el email está registrado: a un email no registrado no se le envía nada, así que no puede fallar. Mientras el proveedor esté caído, ese mensaje revela que el email existe. Se acepta:

- solo pasa durante una falla del proveedor;
- saber que un email existe no da acceso a nada: hace falta el código;
- la alternativa es decir "te llega un código" cuando no llega, y el cliente queda esperando.

## Pendiente

- Magic link para el panel (spec). Ya hay con qué enviarlo.
- Remitente por empresa.
- Reintento automático del envío.
