# 056 — Probar antes de salir en vivo

**Contexto.** El Hito 9 pide un playground en el panel con "Behind the scenes", evals generados automáticamente desde el historial de la empresa y un informe de preparación con un umbral para habilitar "Go live".

**Qué ya estaba.** Los evals generados con el historial y el reporte de preparación (tareas 17 y 19, decisión 023); la vista "detrás de escena" (decisión 041); el chat de prueba `/c/…` y la vista previa del widget.

**Qué se decidió.**

- **Playground:** Reporte → **Probar el asistente**: el chat de la empresa y, al lado, cada paso del caso ("detrás de escena"), en la misma página del panel. Es la vista de la decisión 041 con el chat de la empresa; no hace falta activarla para usarla desde el panel.
- **Umbral para salir:** el último reporte tiene que estimar una resolución de al menos `GO_LIVE_MIN_RESOLUTION` (60 % por defecto) y frenar todos los casos adversariales. Si no, el botón **Salir en vivo** está deshabilitado y dice qué falta.
- **Antes de salir en vivo**, el widget no atiende en el sitio de la empresa (responde 403 con "todavía no está en vivo"). Sí atiende en el panel (vista previa y playground) y en la página `/c/…`, para probar.
- **Volver atrás:** "Pausar" saca al asistente del sitio sin borrar nada.
- **Las empresas de demo** (Acme y Northwind) están siempre en vivo: la demo no cambia.
- **Las empresas que ya tenían el widget instalado** antes de este cambio quedan sin atender hasta salir en vivo. Hoy no hay ninguna fuera de la demo.
