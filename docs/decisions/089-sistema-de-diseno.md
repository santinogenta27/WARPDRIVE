# 089 — Un solo sistema de diseño, accesible y sin lo que el cliente no necesita

**Contexto.** Con las tareas 1 a 6 el panel ya tenía tokens de color y componentes (decisión 085), pero quedaban cosas por resolver:
- no había confirmación antes de lo peligroso;
- los estados vacíos decían "Todavía no hay…" sin explicar ni ofrecer qué hacer;
- las páginas de error eran un texto suelto;
- en Insights, una empresa veía latencias por paso, respaldos entre proveedores de modelos, costo por modelo, trazas de OpenTelemetry y el historial de pruebas.

## Qué se decidió

- **Tokens y componentes** (`html.CSS`): colores (claro por defecto; oscuro con `data-theme="dark"`, a elección de cada usuario), tipografía del sistema, espaciado, botones (primario, secundario, peligro), badges de estado, tablas, KPIs, avisos, estados vacíos y diálogos. Sin colores fijos en estilos en línea.
- **Diálogo de confirmación propio** (`<dialog>` nativo, accesible: foco en Cancelar, Escape cierra). Sale en todo formulario o botón con `data-confirm`, y el texto se traduce como el resto. Pasa:
  - al aprobar un reembolso (en Home, Inbox, Aprobaciones y el detalle del caso);
  - al pausar el agente (Home y Deploy);
  - al borrar un documento, la memoria de un cliente o la empresa;
  - al quitar un producto para sugerir;
  - al desactivar WhatsApp;
  - al volver al modo de prueba de la tienda.

  Se confirma todo reembolso, no solo los grandes. El monto está en la misma tarjeta, y un reembolso no se puede deshacer.
- **"Procesando"**: un formulario enviado queda `aria-busy`, con un indicador en el botón; el doble envío sigue bloqueado.
- **Estados vacíos** con un título, una explicación y un botón de acción. Por ejemplo, Inbox sin conversaciones lleva a probar el agente. **Páginas de error** (403, 404) con el motivo y "Go to Home".
- **Insights para el cliente:** resolución, derivación, casos, satisfacción, ventas atribuidas y costo. El operador ve además:
  - latencia;
  - errores y respaldos entre proveedores;
  - costo por modelo y por caso, con trazas;
  - el historial de pruebas;
  - el detalle del cobro.
- **Accesibilidad**, verificada por el test en las 22 pantallas:
  - un solo `<h1>`;
  - cada control con su etiqueta;
  - cada botón con texto;
  - foco visible;
  - `lang` del idioma del usuario;
  - en el celular, la barra lateral se abre con un botón con `aria-controls` y `aria-expanded`.
