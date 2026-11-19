# 071 — Arreglos de los evals con modelo real (19/11)

**Contexto.** Se corrieron con modelo real todos los casos de Northwind (desde el avance guardado del QA) y Acme completo. Groq (`gpt-oss-20b`) se quedó sin cuota diaria a las 67 llamadas y siguió Gemini (`gemini-3.5-flash-lite`). Cada falla se arregló en el código. No se bajó ningún umbral ni se relajó ningún control.

**Qué se arregló.**

1. **Respaldo Groq → Gemini con herramientas** (`gemini.py`).
   - Qué pasaba: Gemini respondía 400 "Function call is missing a thought_signature" cuando la conversación traía una llamada a herramienta que no hizo Gemini (la hizo Groq antes del respaldo, o vino de la caché).
   - Riesgo: también rompía el respaldo de la API pública en medio de una conversación.
   - Arreglo: esas llamadas llevan la firma de reemplazo que acepta la documentación de Gemini (`skip_thought_signature_validator`).
2. **Falso gerente (nw23)** (decisión 070).
   - Qué pasaba: la reclasificación sin el cargo devolvía `consulta_general` para "un cupón del 50 %" y quedaba la negativa fija.
   - Arreglo: esa segunda clasificación solo elige entre los tipos de caso activos u `otro`.
3. **Pregunta de precio de un producto (nw28).**
   - Qué pasaba: se clasificaba como consulta general y se respondía con el catálogo, sin la búsqueda.
   - Arreglo: el clasificador aclara que una pregunta sobre un producto puntual (precio, talles, colores, stock) va al tipo de caso de productos.
4. **Ventas a medias (nw26, nw29).** Un control nuevo en código (`conduct.sales_correction`) corrige una vez por turno:
   - una recomendación de un producto de la búsqueda sin su precio;
   - prometer armar el carrito o mandar el link sin haberlo hecho ("Let me add it to your cart…");
   - el carrito armado sin la venta cruzada disponible. El guardián sigue controlando el tope del descuento y que se ofrezca una sola vez.
5. **Retención (nw32).**
   - Las alternativas que la retención acaba de ofrecer no se ejecutan en el mismo turno: se ofrecen con palabras. El modelo daba el cupón aunque el cliente acababa de decir "no, gracias".
   - Si el mensaje anterior del agente ya ofreció la alternativa y el cliente insiste, la oferta queda registrada como hecha y la acción sigue. Así no se ofrece dos veces. El guardián controla todo lo demás.

6. **Un test que usaba Groq real** (`test_evals.py::test_la_cli_sale_con_error_si_hay_regresion`).
   - Qué pasaba: borraba las claves del entorno, pero al mandar un código de verificación `crypto` vuelve a cargar el `.env`. La segunda corrida "simulada" usaba Groq.
   - Por qué se veía recién ahora: antes salía todo de la caché y pasaba de casualidad.
   - Arreglo: el test usa `--simulated`.

**Tests.** `backend/tests/test_controles_evals_reales.py` (controles de ventas y retención) y un test nuevo en `test_gemini.py` (firma de reemplazo).

**Qué queda.**
- **nw30:** el modelo vuelve a buscar el gorro antes de ofrecerlo. Es una búsqueda de más que el caso no espera, y en la corrida anterior pasó.
- **nw32:** cancela con una sola oferta de retención, pero no vuelve a consultar el pedido en el segundo turno, y el caso lo espera.
- En nw32 el modelo dio un cupón en el primer turno sin que el cliente lo pidiera. El guardián lo permite porque es una herramienta aprobada de bajo riesgo.
