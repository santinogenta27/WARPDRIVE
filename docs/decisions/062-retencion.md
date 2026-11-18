# 062 — Retención ante una cancelación o devolución

**Contexto.** El punto 4 del Hito 10 pide que, ante una cancelación o una devolución, el agente ofrezca primero las alternativas que la política permite (cambio, crédito, cupón) y que, si el cliente insiste, siga con su pedido sin volver a ofrecer.

**Opciones.**

1. Pedirlo en el prompt. Un modelo chico se lo saltea o vuelve a ofrecer cada vez.
2. Aplicarlo en el guardián, como el resto de la política.

**Recomendación (la elegida): 2.**

- **La política es de cada herramienta.** El campo nuevo del manifiesto `retention` lista las herramientas que se ofrecen antes, por ejemplo `iniciar_reembolso` → `[iniciar_cambio, ofrecer_cupon]`. Se fija como los demás límites (`review.set_tool`) y se conserva al regenerar las herramientas.
- **El guardián, después de los controles de identidad y dueño:** la primera llamada a una acción con retención en un caso y un pedido no se hace. Responde `retencion: true` con las alternativas aprobadas de la empresa, y queda anotado en `retention_offers` (con `tenant_id` y RLS, una fila por caso, herramienta y pedido). La llamada siguiente sigue con su pedido, sin volver a ofrecer. Detrás vienen los controles de siempre: monto, aprobación humana y ejecución.
- **Solo cuentan las alternativas aprobadas.** Si ninguna está aprobada, no hay retención.
- **Un pedido ajeno no llega a la retención:** el control de dueño responde antes, igual que siempre, sin revelar que el pedido existe.
- **"Si el cliente insiste" es en otro mensaje.** Si el modelo vuelve a llamar la acción en el mismo turno, sin esperar al cliente, el ciclo del agente lo frena en código.
- **Una aprobación humana no pasa por la retención:** la persona ya decidió.
- **Northwind:**
  - el reembolso ofrece antes un cambio o un cupón;
  - la cancelación ofrece antes un cupón para quedarse con el pedido;
  - los tipos de caso `refund` y `cancel_order` suman esas herramientas para poder hacer la alternativa si el cliente la elige.
  - El "crédito" queda para cuando la tienda tenga una herramienta de crédito.

**Consecuencias.**

- En Northwind, un reembolso o una cancelación lleva un mensaje más del cliente.
- Los evals nw03, nw04, nw19 y nw21 suman ese mensaje ("No thanks, I just want the refund").
- El guion de la demo en inglés también lo suma (punto 7).

**Pendiente.** Editar la retención de cada herramienta desde el panel. Hoy se fija como `alternatives`, en la configuración de la empresa.
