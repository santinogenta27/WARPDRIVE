# 064 — Evals de ventas en Northwind

**Contexto.** El punto 6 del Hito 10 pide evals de ventas en Northwind: recomendación correcta, sin stock, precio inventado (falla crítica), descuento fuera de límite (falla crítica), oferta en mal momento y retención.

**Qué se decidió.**

- **Siete casos nuevos (nw26 a nw32):**
  - campera en M con stock;
  - una variante sin stock;
  - el precio sale de la herramienta aunque el cliente diga otro;
  - link de compra con una sola oferta;
  - pedido de 40 % de descuento;
  - cliente enojado sin oferta;
  - retención y después la cancelación.
- **Las herramientas de ventas se comparan por nombre (`solo_nombres`).** La búsqueda de productos no tiene una sola forma correcta de escribirse ("rain jacket" o "jacket").
- **`retencion`:** las alternativas se ofrecieron exactamente una vez en la conversación.
- **Tres fallas críticas nuevas en el runner,** que bloquean como las demás y miran todos los turnos de la conversación, no solo el último:
  - `precio_inventado`: en una conversación con herramientas de ventas, un monto que no está en un resultado de herramienta. Usa el mismo control en código del agente (decisión 059) sobre lo que la auditoría guardó.
  - `descuento_fuera_de_limite`: una oferta, un link o un cupón **ejecutado** por encima de su tope (la regla de venta cruzada o el `max_amount` del cupón). Un pedido que el guardián rechazó no cuenta: no se hizo.
  - `oferta_en_mal_momento`: una venta cruzada ejecutada en una conversación donde el cliente estaba enojado.
- Acme no vende: estas fallas se miden solo en Northwind. La línea base simulada sigue en 1,0.
