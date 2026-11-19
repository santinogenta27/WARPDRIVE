# 074 — Primeros pasos en la portada del panel (Hito 11, tarea 3)

**Contexto.** Una empresa piloto entra al panel y no sabe por dónde empezar. La portada (Casos) tiene que mostrar qué le falta para salir en vivo, con el progreso real y un enlace a cada paso. La spec no define cómo se mide cada paso.

**Recomendación.** Cada paso se mide con datos que ya existen. No hay una casilla que se marca a mano: el paso se completa cuando la empresa lo hizo.

| Paso | Hecho cuando | Enlace |
|---|---|---|
| Documentos cargados | hay al menos un documento indexado | Onboarding |
| Reglas de atención | guardó el horario o el email de derivación | Configuración, reglas |
| Marca | guardó el widget: nombre, color, logo o sugerencias | Configuración, widget |
| Prueba en el playground | el chat de la empresa respondió al menos un mensaje | Probar |
| Widget instalado | el widget pidió su configuración desde uno de sus dominios | Configuración, widget |
| Salir en vivo | la empresa salió en vivo | Reporte |

- **Widget instalado.** No basta con cargar un dominio. La primera vez que el widget pide `/chat/widget/config` desde un dominio de la lista de la empresa, se guarda `settings.widget_seen_at`. Eso pasa aunque todavía no esté en vivo, porque el pedido llega igual.
- **Dónde y para quién.**
  - Va arriba de Casos, con "N/6 pasos hechos" y una barra de progreso.
  - Desaparece cuando están los seis.
  - No se muestra en las empresas de demo (Acme y Northwind), que siempre están en vivo y no cambian.
  - Tampoco a las personas del equipo con rol `agent`, que no pueden abrir Configuración ni Onboarding.
- **Idiomas.** Español e inglés, con el catálogo del panel (decisión 037).
