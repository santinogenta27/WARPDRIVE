# 053 — Guías de instalación

**Contexto.** El Hito 9 pide el snippet del widget para copiar, guías cortas para Shopify, WordPress y Tiendanube, y WhatsApp si la empresa tiene número verificado.

**Qué ya estaba.** El snippet con el botón "Copiar código", los dominios permitidos y la vista previa, en Configuración (decisión 031).

**Qué se decidió.**

- **Las guías van en el panel**, debajo del código para copiar, en un desplegable por plataforma: quien instala las tiene al lado del código. También en `docs/instalacion.md` para mandarlas por email.
- **Pasos genéricos**, sin capturas: los menús de cada plataforma cambian seguido. Cada guía dice dónde pegar el código en el pie de todas las páginas.
  - **Shopify:** `layout/theme.liquid`, antes de `</body>`.
  - **WordPress:** el plugin WPCode (sin tocar el tema), o `footer.php`.
  - **Tiendanube:** Configuración → Códigos externos; si el plan no lo tiene, el enlace propio del chat como botón.
- **WhatsApp:** sigue conectándolo el operador (Meta exige verificar la empresa y el número; ver `docs/whatsapp.md`). La guía lo dice. No se hizo el alta autoservicio con Meta: lleva aprobación de Meta y no se puede probar sin una cuenta verificada.
