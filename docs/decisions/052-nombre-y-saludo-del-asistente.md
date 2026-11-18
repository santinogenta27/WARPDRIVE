# 052 — Nombre y saludo del asistente

**Contexto.** El Hito 9 pide configurar tono y marca: nombre del asistente, saludo, idiomas, color y logo del widget y sugerencias iniciales. Color, logo, sugerencias y el nombre que ve el cliente ya estaban (decisión 031); el idioma también (decisión 036), con su tono.

**Qué se decidió.**

- **Nombre del asistente** (por ejemplo "Nina") y **saludo** propio, en Configuración → Widget → Marca. Se guardan en `tenants.settings.brand`. Vacíos, se usan los de siempre.
- **El saludo** es el primer mensaje que ve el cliente al abrir el chat (widget y página `/c/…`). Hasta 300 caracteres, sin `<` ni `>`.
- **El nombre** se le pasa al asistente en el prompt para que se presente así. Es tono, no un permiso: no cambia nada de lo que el asistente puede hacer.
- **Un solo idioma por empresa**, como hasta ahora (decisión 036). Atender en varios idiomas a la vez queda pendiente: el agente ya entiende mensajes mezclados (casos 18 y nw25) y responde en el idioma de la empresa.
- Acme y Northwind no cargan nombre ni saludo: su demo no cambia.
