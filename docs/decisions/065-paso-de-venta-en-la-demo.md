# 065 — Paso de venta en la demo de Northwind

**Contexto.** El punto 7 del Hito 10 pide actualizar la demo de Northwind con un paso de venta: el cliente pregunta por una campera y el agente recomienda una con stock y le manda el link.

**Qué se decidió.**

- **Sugerencia del widget:** *Do you have a rain jacket in size M?* va segunda. El widget admite cuatro, así que sale *What's your return policy?*: esa pregunta se sigue pudiendo escribir.
- **El link lleva a la demo.** En la demo, la API simulada compartida arma los links hacia `{PUBLIC_BASE_URL}/demo/northwind-outfitters/cart/…` (sin `PUBLIC_BASE_URL`, hacia `localhost:8000`). Los tests y evals siguen con `shop.northwind.example`.
- **La página de carrito** (`app/demo/cart.py`) muestra el carrito con los precios del catálogo y un botón **Complete purchase (demo, nothing is charged)**. Al tocarlo, la API simulada anota la compra de la conversación del link. En el panel, **Metrics → Update sales** la muestra como venta atribuida al agente. No se cobra nada ni se pide ningún dato.
- **El guion** (`docs/demo-en.md`) suma "Paso de venta": un clip aparte, en una conversación nueva, que es el caso de evals nw29. `test_guion_demo` controla que sus mensajes sean los del caso.

**Pendiente.** Grabar las capturas del paso de venta con el modelo real (Hito QA).
