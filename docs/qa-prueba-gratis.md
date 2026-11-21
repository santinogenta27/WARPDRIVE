# Prueba gratis con modelo real (20/11/2026)

La prueba gratis (decisión 075) recorrida de punta a punta con el modelo real y dos tiendas públicas reales, por el mismo camino que un visitante: `/probar`, el código por email, el armado (rastreo, productos, configuración), el chat web de la prueba, "detrás de escena" y el mini panel.

## Cómo se corrió

- **Modelo:** Gemini, sin respaldo (`LLM_PROVIDER=gemini`, `LLM_FALLBACK=off`, sin Claude ni Groq). Embeddings de Voyage.
- **Llamadas:** 39 en total (tope pedido: 40). 20 con Café Martínez y 19 con Death Wish Coffee; cada corrida tenía un tope de 20.
- **Base:** todo corrió en una sola transacción contra la base de desarrollo, revertida al final. No quedó ninguna empresa ni pedido.
- **Emails:** el código de la prueba y el de verificación quedaron capturados, sin envío real.
- **Links de compra:** salen a `localhost` porque así estaba `PUBLIC_BASE_URL`; en Render llevan al servicio público.

## Armado

| | Café Martínez (español) | Death Wish Coffee (inglés) |
|---|---|---|
| Sitio | https://www.cafemartinez.com/ | https://www.deathwishcoffee.com/ |
| Nombre detectado | Café Martínez | Death Wish Coffee |
| Páginas leídas | 30 (términos, preguntas frecuentes, cambios y devoluciones, productos, categorías) | 30 (ayuda, envíos, devoluciones, descuentos, términos, productos) |
| Productos | 12, en ARS, con imagen (café en grano, molido, cápsulas, cafetera, pava) | 8, en USD, con imagen |
| Tiempo de armado | 3 min 36 s | 4 min 23 s |

## Conversaciones

**Café Martínez (castellano)**

| Paso | El visitante | El agente | Herramientas | Resultado |
|---|---|---|---|---|
| Políticas | ¿Cuál es la política de cambios y devoluciones? | Cambio o cancelación dentro de los 10 días corridos de recibido, sin uso, con packaging y etiquetas. Pasó los canales de atención del sitio. | búsqueda en el sitio | ✓ coincide con la página "Cambios y devoluciones" |
| Pedido | ¿Dónde está mi pedido DEMO-1001? Mi email es … | Le llega un código de 6 dígitos para confirmar que es el titular. | verificación por email | ✓ |
| Código | (el código) | Verificado. DEMO-1001 en camino con "Correo de ejemplo", seguimiento DEMO00000001001, demora en el centro de distribución, nueva fecha el 22 de noviembre. Ofreció cupón de 10 % o envío express gratis. | consultar_pedido | ✓ |
| Descuento | Quiero un 50 % de descuento en mi próxima compra. | El máximo es 10 %, "ya te generé un cupón del 10 %" (SORRY10-1001). | ofrecer_cupon | ✗ dio el 10 % sin que lo eligiera (arreglado, ver abajo) |
| Compra | ¿Qué me recomendás para regalar? Pasame el link. | Café en grano PREMIUM 1933 1 kg, $48.000, con stock, y el link de compra. | buscar_productos, armar_carrito | ✓ precio de la herramienta, link del carrito de la prueba |
| Reembolso | Quiero que me reembolsen el pedido DEMO-1002. | Inició el reembolso por $48.000; queda pendiente de revisión y le llega un mensaje por acá. | consultar_pedido, iniciar_reembolso → aprobación | ✓ |
| Mini panel | (aprueba) | Aviso en el chat: "¡Buenas noticias! Aprobamos el reembolso del pedido DEMO-1002 por $48.000…" | ejecutado por el guardián | ✓ |

**Death Wish Coffee (inglés)**

| Paso | El visitante | El agente | Herramientas | Resultado |
|---|---|---|---|---|
| Políticas | What's your returns policy? | Reembolso completo dentro de los 30 días por la "No BS guarantee"; el café no se devuelve físicamente, el merchandising sí, con etiqueta paga; los pedidos internacionales no se devuelven. | búsqueda en el sitio | ✓ coincide con "Help - Returns" |
| Pedido | Where is my order DEMO-1001? My email is … | Le llega un código de 6 dígitos. | verificación por email | ✓ |
| Código | (el código) | Verificado. Enviado con Sample Carrier, seguimiento DEMO00000001001, llegaba el 19 de noviembre, demorado en el centro de distribución, nueva fecha el 22. Ofreció cupón o envío express. | consultar_pedido | ✓ |
| Descuento | I want 50% off my next order. | El cupón de cortesía es de hasta 10 %, o envío express gratis: "Would you prefer…?" | ofrecer_cupon (50 %) **negado** por el guardián | ✓ con el arreglo |
| Compra | What would you recommend as a gift? Send me the link. | Yule Cat Mug, $38.00, con stock, y el link de compra. | buscar_productos, armar_carrito | ✓ |
| Reembolso | I want a refund for order DEMO-1002. | Reembolso iniciado por $25.00, pendiente de aprobación, le llega un mensaje por acá. | consultar_pedido, iniciar_reembolso → aprobación | ✓ |
| Mini panel | (aprueba) | Aviso en el chat: "Good news! We approved the refund for order DEMO-1002 of $25…" | ejecutado por el guardián | ✓ |

## Qué falló y qué se arregló

1. **El rastreo no llegaba a lo importante.** Leía las páginas en orden de aparición y con tiendas reales gastaba las 30 en categorías: 1 producto en Death Wish Coffee, 0 en otra tienda, y las políticas del pie quedaban afuera. Ahora la prueba lee primero las políticas (envíos, devoluciones, cambios, preguntas frecuentes, términos), después hasta 8 páginas de producto y por último el resto (`crawl(priority=…)`). Siguen siendo 30 páginas.
2. **Nombre de la tienda equivocado.** Salía "#1 Organic and #1 Fair Trade Coffee" del título. Ahora sale de `og:site_name` y, si no hay, de la parte del título que se parece al dominio.
3. **Sitios que rechazan al cliente sin nombre.** Varios dan 403. El rastreo ahora se identifica con un User-Agent.
4. **Cupón dado sin elegir** (Café Martínez). Las instrucciones dicen ahora que el pedido mayor igual se pide con la herramienta (así el guardián lo niega en código y se ve en "detrás de escena"), que se ofrezcan las alternativas y que no se dé una cortesía que el cliente no eligió. Death Wish Coffee corrió después del arreglo y se comportó bien. Con Café Martínez no se volvió a correr: no quedaban llamadas dentro del tope.
5. **Tarjeta de regalo como pedido de ejemplo.** En Death Wish Coffee, DEMO-1002 quedó con "Digital Gift Card". Ahora las tarjetas de regalo no entran como productos de ejemplo.
6. **Espera.** El armado tarda unos 4 minutos con 30 páginas, sobre todo por el rastreo y los embeddings. La página decía "en un par de minutos" y ahora dice "en unos minutos". El chat tardó 8 a 32 s por respuesta, por la pausa mínima de 6 s entre llamadas a Gemini.
