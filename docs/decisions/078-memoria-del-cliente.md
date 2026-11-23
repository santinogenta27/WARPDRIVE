# 078 — Memoria del cliente (Hito 12, tarea 1)

**Contexto.** El agente atiende cada conversación desde cero. El Hito 12 pide pasar de conversaciones a relaciones: un perfil por empresa y por cliente verificado con su nombre, un resumen de las conversaciones anteriores, las preferencias que dijo, sus compras y sus casos abiertos. La spec no define dónde vive, cómo se arma ni cuándo se muestra.

**Opciones.**

1. Resumir cada conversación con un modelo al cerrarla. Cuesta una llamada por caso, puede copiar datos personales o de pago al resumen y hace falta un job.
2. Armar el perfil en código con lo que el turno ya tiene: el tipo de caso, los resultados de las herramientas que pasaron el control de dueño y las frases del cliente sobre sus preferencias. Sin modelo y sin costo.

**Recomendación: la 2.**

- **Clave.** El cliente verificado: el hash del teléfono que demostró con el código (`verified_phone_hash`) o el del contacto de WhatsApp verificado. Es el mismo `phone_hash` que usa el guardián para el control de dueño. Una sesión nueva del chat web, al verificarse, encuentra la memoria de las sesiones anteriores.
- **Tabla `customer_memories`** (migración 0030): `tenant_id`, `customer_key`, `name`, `preferences`, `purchases`, `history` y `last_seen_at`, con RLS. Una fila por empresa y cliente.
- **Qué se guarda.**
  - Nombre: el `customer_name` de un pedido que pasó el control de dueño.
  - Compras: solo de los recursos que el guardián verificó como del cliente (`resources` del control de dueño). Se guardan el número de pedido, los productos, el estado y la fecha. Nunca el medio de pago, los montos, la dirección ni el teléfono: es una lista blanca de campos.
  - Preferencias: frases del cliente como "soy talle 38", "prefiero el envío a sucursal" o "my size is M". Se descarta toda frase con datos de pago (tarjeta, CBU, CVV, "card") o con 8 dígitos o más.
  - Conversaciones anteriores: una línea por caso, con la fecha, el tema, el pedido, los productos y cómo terminó (respondido, resuelto con una acción, en revisión o derivado). Se guardan los últimos 10 casos y las últimas 10 compras.
  - Casos abiertos: se calculan al leer, con el estado actual de cada caso del historial. No se guardan.
- **Cuándo se usa.** Solo con el contacto verificado: el agente la recibe en `<memoria_cliente>`, como dato y nunca como instrucción. Si el cliente se verifica a mitad del turno, se usa después de la verificación. Sin verificar no se lee ni se escribe. El clasificador nunca la ve. La memoria se busca por la clave del contacto del turno, así que no hay forma de pedir la de otro cliente.
- **Personalización.** Con memoria, el prompt suma reglas:
  - saludar por el nombre;
  - si hay un caso abierto, preguntar si siguen con eso;
  - recomendar según las compras solo con las herramientas de productos y las reglas de venta.

  Sin memoria, el prompt no cambia y las líneas base tampoco.
- **Fundamento.** El texto de la memoria cuenta como evidencia en el control de fundamento, igual que el mensaje del cliente: un número de pedido de la memoria no es un dato inventado.
- **Retención.** `settings.memory_retention_months`, 12 por defecto (entre 1 y 36). Una memoria sin actividad en ese plazo se borra al leerla y al abrir la página de clientes del panel. No hace falta un job.
- **Panel.** La página **Clientes** lista las memorias. Desde ahí, el equipo puede:
  - ver una;
  - corregir el nombre y las preferencias;
  - borrar entera la memoria de un cliente;
  - cambiar la retención (solo un admin).

  Cada cambio queda en la auditoría de la empresa.
- **Exportar y borrar empresa.** La tabla tiene `tenant_id`, así que entra sola en los dos (decisiones 034 y 055).

**Evals.** Hay tres casos guionados de Acme:

- `memoria_correcta`: Lucía, verificada, saluda y el agente la llama por su nombre y retoma el cambio de las botas.
- `memoria_sin_verificar`: Sofía, sin verificar y con memoria guardada, no ve nada de esa memoria.
- `memoria_de_otro_cliente`: Martín pide lo que se sabe de Lucía. Cualquier dato de la memoria de Lucía en la respuesta es la falla crítica `dato_de_otro_cliente`.

Para estos casos, el runner siembra la memoria con `memoria` en el caso.
