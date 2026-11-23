# 085 — El panel desde los ojos del cliente: navegación, idioma por usuario y sistema de diseño

**Contexto.** El panel creció pantalla por pantalla (hitos 6 a 12): una barra superior con 13 links, una página de Configuración de unos 7000 px con 15 temas, textos en español con jerga interna ("guardián", "tipos de caso", "onboarding") y avisos de demo. El Hito 13 lo rediseña para el cliente que compra el producto: simple, ordenado, en inglés y con calidad de producto profesional (Sierra, Linear, Intercom).

## 1. Identidad y navegación

- **Título:** "Enterprise Data AI", con el producto "AI Support & Sales Agent". La empresa del cliente es una etiqueta debajo, no el título. Fuera "Panel del equipo" y los avisos de demo.
- **Barra lateral con 9 secciones**, cada una con su dirección en inglés y, si hace falta, pestañas:

| Sección | Dirección | Pestañas | Lo que había antes |
|---|---|---|---|
| Home | `/panel/home` | — | (nuevo) primeros pasos, pendientes, números de la semana |
| Inbox | `/panel/inbox` | Conversations · Approvals · Drafts | Casos, Aprobaciones, borradores del modo supervisado |
| Customers | `/panel/customers` | — | Clientes |
| Agent | `/panel/agent` | Rules · Actions · Topics · Brand & voice · Sales · Language | reglas, modo supervisado, supervisor, herramientas, tipos de caso, voz, ventas cruzadas, idioma |
| Knowledge | `/panel/knowledge` | — | Onboarding |
| Deploy | `/panel/deploy` | — | sistema de la empresa, widget, WhatsApp, salir en vivo |
| Test & Improve | `/panel/test` | Readiness · Playground · Simulations · To improve | Reporte, playground, simulaciones, Para mejorar |
| Insights | `/panel/insights` | Overview · Results | Métricas, Resultados |
| Settings | `/panel/settings` | Team · Usage & model · Your data | Equipo, modelo y gasto, exportar y borrar |

- **Zona de operador**, separada en la barra lateral bajo "Operator" y solo para el operador de la plataforma: Companies, Trials, Billing y Behind the scenes (`/panel/operator/...`). Un cliente no la ve y abrirla responde 403.
- **Una persona del equipo** (rol `agent`, decisión 058) no ve ni abre Agent, Knowledge, Deploy ni Settings.
- **Direcciones de antes:** cada `GET` de antes (`/panel/casos`, `/panel/configuracion`…) redirige a la nueva con los mismos parámetros, así siguen sirviendo los links de los emails. Las direcciones de los formularios (`POST /panel/configuracion/...`) no cambian: cada uno vuelve a la pantalla donde está.
- **Tus datos:** el admin de una empresa puede descargar sus datos (el mismo .zip sin secretos de la decisión 055) y borrar su empresa escribiendo el nombre. Antes solo lo hacía el operador; es lo esperable de un producto que guarda datos de clientes.

## 2. Idioma por usuario

- **Opciones:** (a) seguir con el idioma de la empresa (decisión 037); (b) idioma por usuario guardado en la sesión; (c) por usuario, guardado en `users` y en la sesión.
- **Elegida: (c).** El panel sale en inglés salvo que el usuario elija Español en el menú. Se guarda en `users.preferences` (migración 0035, aditiva) y en la cookie de sesión; el operador no tiene fila en `users` y lo guarda solo en la cookie. El idioma de la empresa sigue existiendo: es el que usa su agente con los clientes (Agent → Language).
- **Cómo se traduce:** las pantallas de antes siguen escritas en español y `english.py` las pasa al inglés. Lo nuevo se escribe en inglés y `spanish.py` lo pasa al español. `html.translate` aplica el catálogo del idioma del usuario; lo marcado con `translate="no"` (por ejemplo, el botón "Español") no se toca.
- **Tests:** los de antes miran textos en español, así que `conftest.py` fija el español como idioma por defecto. Los del Hito 13 vuelven al inglés.

## 3. Sistema de diseño

- Un solo juego de tokens (colores, radios, sombras) en `html.CSS`, con tema claro por defecto y oscuro opcional por usuario (`data-theme="dark"`, guardado en sus preferencias).
- Componentes: barra lateral, pestañas, encabezado de pantalla, tarjeta, badge de estado, botones (primario, secundario, peligro), tablas, KPIs, avisos, estado vacío.
- Sin recursos externos (decisión 022): tipografía del sistema e iconos SVG propios en línea.
- En el celular, la barra lateral se abre con el botón del menú; Escape o un toque afuera la cierran.
