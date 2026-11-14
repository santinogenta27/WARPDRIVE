# 041 — Vista "detrás de escena" de la demo

**Estado:** adoptada (13 de noviembre de 2026). Hito 8, tarea 6. Decidida sin aprobación previa: revisala.

## Contexto

En el video de la demo, el cliente potencial tiene que ver qué hace el asistente por dentro mientras conversa. Eso incluye la intención que detecta, la verificación, las herramientas, las reglas del guardián, las fuentes, la aprobación, la latencia y el costo. Hoy eso está en la auditoría del panel, en otra pantalla y después de que pasa.

## Decisiones

### 1. Una página propia: `/demo/{enlace del chat}`

- **A la izquierda**, el sitio de la tienda: navegación, portada y productos del catálogo de Northwind. El widget de siempre (`/widget.js`) está abierto en modo `inline`: es el mismo chat que se instala en un sitio.
- **A la derecha**, el panel en vivo. Cada mensaje del cliente es una tarjeta con:
  - la intención detectada (y el segundo tema, si lo hay);
  - la identidad: verificado o no, y los pasos de la verificación;
  - los documentos buscados y las fuentes citadas;
  - cada herramienta, con sus parámetros, su estado y las reglas del guardián: ✓ pasó, ✕ la frenó, · no se llegó a controlar;
  - lo que el guardián ofreció en su lugar (el tope del cupón) y los controles que reescribieron una respuesta;
  - la aprobación pendiente, aprobada o rechazada;
  - el resultado, la latencia, el costo y los tokens.
- **Mismo diseño que el widget:** vidrio, aro de luz en el borde, radios amplios, el color de la empresa y tema claro u oscuro según el sistema.

### 2. Cómo se arma: la auditoría del caso, en vivo

- El panel pregunta cada 1,5 s a `GET /demo/{enlace}/live?session=…`.
- La sesión es la del widget: la página la lee del `localStorage` del mismo origen.
- El servidor busca el caso abierto de esa sesión y arma los turnos desde `audit_events` (`app/demo/behind.py`). No hay tabla nueva ni otro registro que se pueda desfasar: lo que se ve es exactamente lo auditado.
- **Las reglas del guardián** salen del manifiesto de cada herramienta (identidad, dueño, tope, aprobación) y de lo que decidió el guardián en esa llamada.
- **Los motivos internos**, que están en español, se muestran traducidos en una empresa en inglés.

### 3. Solo lectura y sin datos personales

- **Solo lectura:** la página no tiene formularios y la ruta solo acepta `GET`.
- **El texto del cliente no se muestra:** puede traer cualquier dato (un nombre, una dirección) y ya se ve en el chat, a la izquierda.
- **Los parámetros personales** se reemplazan por `•••`: dirección, email, teléfono y nombre. El número de pedido y el monto se ven.
- **Los resultados de las herramientas no se muestran:** solo su estado, el tope y las alternativas. Un error del sistema se muestra sin emails, teléfonos ni códigos.
- **Solo la sesión propia:** la sesión tiene que ser del chat de esa empresa. Si no, da 401. Otra sesión ve su propia conversación, nunca la de otro.

### 4. Activable por empresa, apagada por defecto

- **Dónde vive:** `tenants.settings.behind_the_scenes`. Sin el campo, está apagada y la página y la ruta dan 404.
- **Panel → Configuración → "Vista detrás de escena":** activar o desactivar, con el enlace a la página.
- **Northwind la trae activada:** `company.json` la prende y `make demo-setup` la deja así.

## Límites

- **Consulta cada 1,5 s.** Alcanza para la demo y para pocas personas mirando. No es para producción con muchos clientes.
- **Muestra el caso abierto de la sesión.** Si el cliente empieza una conversación nueva con el botón del widget, el panel pasa a la nueva.
- **La portada solo trae productos para Northwind.** Otra empresa ve la portada con su nombre y su color, sin catálogo.
