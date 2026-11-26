# 102 — Key propia completa: modelo, prueba de la key, vista de gasto y tabla de precios

**Contexto.** La decisión 049 dejó que cada empresa cargue su key (Anthropic, OpenAI, Groq o Gemini),
cifrada, con un tope mensual. Faltaba lo que un cliente espera de un producto profesional: elegir el
modelo, probar la key antes de guardarla, verla enmascarada, quitarla, ver el gasto con detalle y
saber de quién es la factura. Los precios estaban en un diccionario de `pricing.py` sin fecha ni
fuente, y el de lectura de caché de Sonnet 5.5 estaba mal (0,20 en lugar de 0,10).

**Qué se decidió.**

- **Modelo a elegir de una lista corta por proveedor** (`tenant_llm.CATALOG`), con una línea que
  explica para qué sirve cada uno. El elegido responde las conversaciones (el agente, y los modelos
  que pedía como `claude-sonnet-5-5` y `claude-opus-5-5`). Para clasificar mensajes y revisar
  respuestas, cada proveedor usa su modelo liviano (`tenant_llm.LIGHT`), como ya hacía la plataforma.
  Una key guardada antes de esta decisión sigue funcionando con el modelo recomendado.
- **"Test key"** hace una llamada mínima (una respuesta de pocos tokens, por `LLMClient` y con el uso
  en `usage`). Muestra "Connected" o el error en palabras simples: key inválida, sin saldo o cuota,
  sin acceso al modelo, límite de pedidos o proveedor caído. "Save" vuelve a probar (salvo que esa
  misma key se haya probado bien en los últimos 10 minutos) y **no guarda una key que no pasó**.
  Cambiar de modelo con la key guardada también se prueba antes.
- **La key nunca sale:** se guarda cifrada con la clave de la empresa. Al panel solo llega una pista
  (`sk-…a1b2`: los 3 primeros caracteres y los 4 últimos), y nunca se escribe en logs, respuestas,
  HTML ni `audit_events`. Los errores del proveedor se traducen; su texto crudo no se muestra.
- **"Replace" y "Remove".** Quitar la key vuelve al modelo de la plataforma, con sus límites.
- **Solo los admins de la empresa** ven y cambian esto (Settings ya es solo para admins, decisión
  058). Cada cambio queda en `audit_events` como evento de la empresa (sin caso): `llm_key_saved`,
  `llm_key_removed`, `llm_model_changed` y `llm_cap_changed`. Se registra quién lo hizo, el proveedor
  y el modelo, nunca la key.
- **Embeddings:** siguen siendo los de la plataforma para todas las empresas (lo decidió Santino el
  25/11/2026). Usar la key de la empresa obliga a cambiar dónde se elige el embedder, fuera del
  alcance de esta tarea. Solo sería compatible con Gemini (`gemini-embedding-2`, el mismo modelo de
  la plataforma); OpenAI usa otros vectores y Anthropic y Groq no tienen embeddings. Settings lo
  explica en una línea. No se reindexa nada.
- **De quién es el gasto.** Con key propia, el uso se registra como `byok/<proveedor>/<modelo>` y se
  calcula con el precio de lista del proveedor: con su key, la empresa paga la tarifa paga, aunque
  la plataforma use el nivel gratuito de Gemini o Groq. La vista dice "Billed to your Anthropic
  account" (o el proveedor que sea) o "Included in your plan".
- **El tope** se calcula igual que antes (decisión 049), ahora también con key propia y Gemini o
  Groq, porque su costo ya no es 0.
- **Vista "Usage"** (Settings → Usage):
  - gasto del mes y proyección al fin de mes;
  - tope con barra y aviso al 80 %;
  - gasto por día, en un gráfico con su tabla;
  - por modelo, tokens de entrada y de salida;
  - costo promedio por conversación (casos con uso en el mes) y por caso resuelto (el gasto del mes
    dividido por los casos resueltos en el mes);
  - las conversaciones más caras, con link al caso;
  - exportar el mes en CSV.

  Todo sale de `usage`, filtrado por la empresa (RLS). La vista aclara que es una estimación y que
  el monto exacto está en la factura del proveedor.
- **Precios en una tabla de configuración:** `backend/app/llm/prices.csv`, con modelo, precios,
  desde cuándo rigen, fuente y fecha de verificación. `pricing.py` solo la lee. Un modelo sin precio
  en la tabla aparece como "price unknown" en la vista y en el CSV, nunca como 0, y queda fuera del
  total (la vista avisa cuántas llamadas quedaron afuera).

**Modelos y precios verificados el 25/11/2026** (USD por millón de tokens: entrada / salida /
escritura de caché / lectura de caché):

| Proveedor | Modelo | Precio | Fuente |
|---|---|---|---|
| Anthropic | `claude-sonnet-5-5` | 2 / 10 / 2,50 / 0,10 | platform.claude.com/docs/en/about-claude/pricing |
| Anthropic | `claude-haiku-5-5` | 0,10 / 0,50 / 0,125 / 0,01 (prompts de hasta 100.000 tokens) | ídem |
| Anthropic | `claude-opus-5-5` | 4 / 20 / 5 / 0,20 | ídem |
| OpenAI | `gpt-5-mini` | 0,25 / 2,00 / — / 0,025 | developers.openai.com/api/docs/pricing |
| OpenAI | `gpt-5.4-mini` | 0,75 / 4,50 / — / 0,075 | ídem |
| OpenAI | `gpt-5-nano` (liviano) | 0,05 / 0,40 / — / 0,005 | ídem |
| Gemini | `gemini-3.8-flash` | 0,75 / 3,75 / — / 0,075 hasta el 31/12/2026; 1,50 / 7,50 / — / 0,15 desde el 1/1/2027 | aistudio.google.com/docs/pricing |
| Gemini | `gemini-3.6-flash` | igual que 3.8 Flash | ídem |
| Gemini | `gemini-3.5-flash-lite` | 0,30 / 2,50 / — / 0,03 | ídem |
| Groq | `openai/gpt-oss-120b` | 0,15 / 0,60 (sin precio de caché publicado) | console.groq.com/docs/models |
| Groq | `openai/gpt-oss-20b` | 0,075 / 0,30 | ídem |

Notas:
- OpenAI, Gemini y Groq no cobran la escritura de caché aparte: en la tabla vale lo mismo que la
  entrada.
- Haiku 5.5 cobra 0,50 / 2,50 los prompts de más de 100.000 tokens. Los del agente están muy por
  debajo, así que la tabla usa el precio de hasta 100.000.
- Los niveles gratuitos de Gemini y Groq que usa la plataforma siguen en 0 (decisiones 012 y 020).
- Qwen 3.8 en Groq está en vista previa (0,80 / 4,00) y no entra en la lista.

**Precios a revisar:** los proveedores los cambian sin aviso. Al actualizar `prices.csv`, se agrega
una fila con su fecha `since`; las filas viejas no se borran, así el costo de cada día se calcula con
la tarifa que regía ese día.
