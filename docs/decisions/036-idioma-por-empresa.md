# 036 — Idioma por empresa (es / en)

**Estado:** adoptada (12 de noviembre de 2026). Hito 8, tarea 1. Decidida sin aprobación previa: revisala.

## Contexto

Todo lo que ve el cliente estaba escrito en español: el prompt del agente, los mensajes fijos (verificación, derivación, cierre, CSAT), los filtros en código que leen la respuesta del modelo, el widget y el email del código. El Hito 8 suma una demo en inglés para clientes internacionales, sin cambiar nada de Acme.

## Decisiones

### 1. El idioma vive en `tenants.settings.language`

- Valores: `es` (por defecto) o `en`. Sin migración: `settings` ya es JSON.
- Se elige en el panel, en **Configuración → Idioma**. Cambiarlo cambia el tono: `en` usa el tono `english`; volver a `es` usa `rioplatense`.
- Una empresa sin el campo (Acme y todas las existentes) sigue en español, sin cambios.

### 2. Un módulo de textos: `app/i18n.py`

- Todos los mensajes fijos que llegan al cliente están ahí, en los dos idiomas, con la misma clave.
- Los módulos que ya exportaban un texto en español (`closing.CLOSING_MESSAGE`, `csat.QUESTION`, los de `loop.py`) lo siguen exportando: los tests existentes no cambian.

### 3. El prompt en inglés es otro archivo, no otra versión

- `system_v5_en.txt` es la traducción de `system_v5.txt`, con el tono `english`: cálido y profesional, sin voseo ni post-procesado.
- `SYSTEM_PROMPT_VERSION` sigue en `system_v5`: el historial de evals y la línea base de Acme no cambian.
- Los nombres internos quedan en español, porque son identificadores y no texto: herramientas (`responder`, `derivar`, `consultar_pedido`), campos (`mensaje`, `fuentes`, `factual`), etiquetas del prompt (`<documentos>`, `<mensaje_cliente>`) y las claves del YAML de los tipos de caso. El prompt en inglés explica qué es cada una.

### 4. Los filtros en código tienen su versión en inglés

Cada filtro elige su patrón según el idioma de la empresa:

- Pedir otra vez la verificación a un cliente verificado.
- Amenaza legal: siempre deriva.
- Pedir datos personales en texto.
- Email que el cliente manda solo.
- Confirmación de cierre.
- Respuesta al CSAT.
- `presentable.py`: metadatos en inglés (`sources:`, `action:`).

El control de fundamento también:

- Montos con coma de miles (`$1,250.00`).
- Fechas escritas con el mes: "November 14", "Nov 14", "14 de noviembre". El día y el mes tienen que coincidir con una fecha de la evidencia. Antes solo se controlaba el número del día, así que "December 14" pasaba con una herramienta que decía 2026-11-14. Esto vale en los dos idiomas.

### 5. El clasificador tiene su prompt en inglés

Las etiquetas no cambian: son los nombres de los tipos de caso de la empresa y las cinco fijas.

### 6. Widget y página `/c/…`

`/chat/widget/config` devuelve `lang`. El widget tiene sus textos en los dos idiomas y elige con `lang`. Antes de cargar la configuración (estado de carga o error) usa `data-lang` o el `lang` de la página. La página `/c/…` sale con `<html lang>` y el título en el idioma de la empresa.

### 7. Fuera de alcance

- **Plantillas de WhatsApp** (`caso_actualizado`): en español. Las aprueba Meta y el canal está en pausa.
- **Razones internas del guardián**: solo las ven el modelo y la auditoría. Quedan en español; la vista "detrás de escena" (tarea 6) las muestra traducidas.
- **Panel**: es la tarea 2.

### 8. Ramas apiladas

Cada tarea del Hito 8 depende de la anterior: Northwind necesita el idioma, sus herramientas necesitan a Northwind, y así hasta el guion. Por eso cada rama sale de la anterior y no de `main`:

`idioma-empresa` → `panel-ingles` → `northwind` → `herramientas-northwind` → `comportamiento-agente` → `detras-de-escena` → `guion-demo-en`

Cada pull request va contra `main`. Hasta mergear el anterior, el pull request muestra también los cambios de las tareas previas. Conviene aprobarlos en orden; después de cada merge, el siguiente muestra solo lo suyo.
