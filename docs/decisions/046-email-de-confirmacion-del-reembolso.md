# 046 — Email de confirmación al aprobar un reembolso

**Estado:** adoptada (16 de noviembre de 2026), para el cierre del video de la demo en inglés. Decidida sin aprobación previa: revisala.

## Contexto

En el guion de la demo en inglés (`docs/demo-en.md`), una persona aprueba en el panel el reembolso de NW-4528. Hasta ahora el cliente se enteraba solo por el chat. El cierre del video necesita además un email de confirmación que llegue al mismo iPhone que recibió el código.

La spec no define este email. Tampoco había de dónde sacar la dirección: al verificar, el email que escribe el cliente se usa para mandar el código y se descarta. Solo queda el hash del teléfono del cliente.

## Opciones para la dirección

1. **Pedírsela al sistema de la empresa por el teléfono del pedido.** La API de Northwind solo busca clientes por email, y con `NORTHWIND_DEMO_CUSTOMER_EMAIL` (decisión 043) la clienta del guion tiene dos emails: no se sabría a cuál mandar.
2. **Guardar en la verificación el email con el que el cliente se verificó, cifrado.** Es la dirección que el cliente probó que es suya con el código.
3. **Guardarlo en claro.** Descartada: es un dato personal.

## Decisión: opción 2

### La dirección

- Migración `0021`: columna `identity_verifications.email_enc`.
- Se guarda al mandar el código, solo si el email es de un cliente del sistema. Va cifrado con la clave propia de la empresa (`crypto.encrypt_for`): lo cifrado para una empresa no se descifra con la de otra.
- Para el email de confirmación se usa el de la última verificación exitosa del contacto, mientras dure (30 días). Un contacto verificado de otra forma (por ejemplo, por el teléfono de WhatsApp) no tiene email: no se manda nada.

### Cuándo se envía

- Al aprobar en el panel un `iniciar_reembolso` que el guardián ejecuta con éxito.
- Si se rechaza o la ejecución falla, no se envía.
- **Una sola vez por aprobación:** la fila de la aprobación se bloquea mientras se decide, y el envío queda en `audit_events` (`refund_confirmation_sent`). Aprobar dos veces o reintentar el envío no manda otro email.
- **Si el proveedor falla:** la aprobación sigue valiendo. Queda `refund_confirmation_failed` en la auditoría y un `MailError` en `error_events`, como con el código. El email no llega.
- **La auditoría no guarda la dirección:** solo la aprobación, el pedido, el monto y la referencia.

### El email

Mismo mailer (Resend o consola) y mismo estilo que el del código (decisión 043): tablas, estilos en línea, sin imágenes. En el idioma de la empresa.

- **Asunto:** `Your refund is confirmed – order NW-4528` (en español: `Tu reembolso está confirmado – pedido 1005`).
- **Arriba:** el nombre de la empresa y una línea con el pedido.
- **El monto,** grande.
- **Tres datos:**
  - el medio de pago;
  - el plazo de acreditación;
  - el número de referencia.
- **Al pie:** que responda por el chat con la referencia si tiene dudas.

Los datos salen de la respuesta de la herramienta (el sistema de la empresa). La API simulada de Northwind ahora devuelve `payment_method` y `expected_within` en cada reembolso. Si el sistema de una empresa no los devuelve, el email dice "your original payment method" y "a few business days, depending on your bank". La referencia es el `id` del reembolso o, si no viene, una derivada de la aprobación.

### En el panel y en "Behind the scenes"

- El detalle del caso muestra el paso "Sent the refund confirmation email", con la referencia.
- "Behind the scenes" suma "Confirmation email sent" junto a "Approved by a person".
