# 037 — Panel en inglés

**Estado:** adoptada (12 de noviembre de 2026). Hito 8, tarea 2. Decidida sin aprobación previa: revisala.

## Contexto

El panel son páginas HTML servidas por la API (decisión 022), con el texto en español escrito adentro de cada ruta. El video de la demo en inglés también muestra el panel, que tiene que verse en el idioma de la empresa y con el mismo diseño.

## Opciones

1. **Pasar el idioma a cada función y envolver cada texto.** Es lo más limpio, pero toca cada línea de cinco archivos grandes (unas 2.000 líneas). El cambio sería enorme y fácil de romper en las pantallas en español.
2. **Plantillas separadas por idioma.** Duplica cada página: dos versiones que se desfasan con el tiempo.
3. **Un catálogo que traduce el texto de la página ya armada.** Las páginas no cambian. Al final, `page()` reemplaza cada texto de la interfaz por su versión en inglés.

## Decisión: la opción 3, con las ayudas comunes traducidas en origen

- **El idioma del panel es el de la empresa de la sesión.** `enter()` ya consultaba la empresa en cada pedido; ahora también lee su idioma y lo deja en una `ContextVar`, que vale solo para ese pedido. El operador entra a Acme, así que ve el panel en español.
- **Las ayudas comunes ya salen en inglés:** estado del caso, quién lo resolvió, montos (`$1,250.50`, con centavos), fechas (`Nov 12, 14:05`), nombres de tipos de caso y acciones de herramientas (`Refund · Order NW-4521 · $189.50`). Son datos que se arman con piezas y no se pueden traducir después.
- **El resto, con el catálogo** (`app/panel/english.py`):
  - Textos enteros, más patrones para los que llevan datos ("Motivo: …", "3 de 4", el tiempo por paso).
  - Se busca cada nodo de texto entero, con los espacios normalizados.
  - **Lo que no está en el catálogo queda igual.** Así los datos de la empresa y de sus clientes no se traducen por accidente.
  - No se toca el texto de los mensajes del chat (`class="msg"`), ni `<script>`, `<style>`, `<textarea>` o `<pre>`.
- **Mismo diseño:** es el mismo HTML. El test compara la estructura de etiquetas y clases en los dos idiomas.
- **Login:** antes de entrar no se sabe la empresa. La pantalla tiene un enlace **English / Español** (`/panel/login?lang=en`).

## Cómo se evita que quede texto sin traducir

`test_panel_ingles.py` arma una empresa en inglés con:

- un tipo de caso y herramientas;
- una conversación derivada;
- una aprobación pendiente.

Después recorre Casos, el detalle con la auditoría, Aprobaciones, Onboarding, Reporte, Configuración y Métricas. Falla si en el texto visible queda una palabra en español. Del texto visible se excluyen los mensajes, el código, los campos y lo citado entre comillas. Cuando se agregue un texto nuevo al panel, el test avisa si falta en el catálogo.

## Límites

- **Pantallas solo del operador:** Empresas, Facturación, Reiniciar demo y Borrar empresa quedan en español. El operador entra siempre a Acme.
- **Horario:** el panel muestra la hora de Buenos Aires en los dos idiomas.
- **Instrucciones de los tipos de caso:** las claves del YAML siguen en español (`objetivo`, `herramientas`): son el formato que lee el agente (decisión 036).
