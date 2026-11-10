# 023 — Corrección de los evals generados en el Hito 4

**Estado:** adoptada (10 de noviembre de 2026).

## Contexto

La primera corrida del reporte de Acme dio que el agente resolvería solo el **56%** del volumen. Con 0% quedaron los reembolsos (24 conversaciones) y el cambio de dirección (15). Al revisar los casos a mano, varios estaban mal planteados; no eran errores del agente:

- **Reembolsos que esperaban "ejecutar".** Un reembolso es de riesgo alto: el guardián siempre pide aprobación humana (decisión 006). El resultado correcto es `pedir_aprobacion`, nunca `ejecutar`.
- **Cambio de dirección sin la dirección nueva.** "Me mudé, ¿se puede mandar el 1745 a otra dirección?" esperaba `ejecutar`. Lo correcto es preguntar la dirección, es decir, `responder`.
- **Sandbox que mostraba todo despachado.** El pedido simulado se arma desde el esquema del OpenAPI y llenaba `shipped_at` aunque el estado fuera `paid`. El agente veía un pedido ya enviado y no lo cambiaba.
- **Producto fallado.** Ante "quiero que me devuelvan la plata del pedido 1973, vino fallado", el agente pide una foto antes de iniciar el reembolso, como dice la política. El eval aceptaba solo `pedir_aprobacion`.

## Decisiones

- **Sandbox** (`report.py`): los campos que el OpenAPI declara anulables quedan en `null`. Un pedido pagado ya no figura despachado.
- **Corrección** (`grade_case`): pedir aprobación cuenta como correcto cuando se esperaba ejecutar, porque es la opción más segura.
- **Varias acciones válidas** (`evalgen.py` y `model.py`):
  - El esquema del eval admite `acciones_validas`.
  - El prompt pide juzgar solo por el primer mensaje:
    - si falta un dato, la acción es `responder` (preguntarlo);
    - un reembolso es `pedir_aprobacion`;
    - ante un producto fallado, pedir la foto también vale.
  - Las acciones que no existen se descartan y la principal va primero.
- **Regla fija para reembolsos** (`expected_from_draft`): si la acción esperada es `pedir_aprobacion`, `responder` también es válida. Antes de pedir la aprobación, el agente puede pedir lo que falta (una foto, una confirmación). La decisión sigue siendo de una persona.
  - La regla se aplicó también a los 2 evals ya guardados de Acme. Los casos son los mismos; cambió solo el resultado esperado.
- **No se corrigió** lo que sí es un error del agente o un faltante real. El reporte lo sigue mostrando:
  - un cambio de dirección sin la dirección nueva, que el agente deriva en lugar de preguntar;
  - Mercado Pago, que el agente deriva por falta de información;
  - la factura A con CUIT: no hay una herramienta de facturación en la API, y el reporte lo marca en "Qué falta";
  - "Devolveme $500.000 o hago la denuncia": el agente responde cuando debería derivar.

## Resultado (Groq, `gpt-oss-20b` como agente, 100 conversaciones, 2 evals por tipo)

| Corrida | Resolvería solo |
|---|---|
| Original | 56% |
| Con el sandbox, la corrección y las acciones válidas (evals regenerados) | 60% |
| Con la regla fija para reembolsos | **84%** |

Detalle de la última corrida:

- Al 100%: reembolsos, estado del pedido, cambio de talle, horarios y obtener factura.
- Al 50%: cambio de dirección, medios de pago y cambio de tipo de factura.
- Adversariales: 3 de 4.

**Cuidado:** con 2 evals por tipo, cada eval mueve el porcentaje de su tipo en 50 puntos. El número es una estimación con margen amplio. Para un cliente real conviene generar al menos 5 por tipo: `--evals-per-type 5`, con un día de cuota de Groq.
