# 032 — Facturación por período

**Estado:** adoptada (11 de noviembre de 2026). Hito 7, tarea 2. Tarea 22 de la spec.

## Contexto

La spec dice: "Se cuenta como resuelto autónomamente si ningún humano intervino y no se reabre en 7 días. Esa es la unidad de cobro." La decisión 027 dejó la reapertura y un resumen del total por empresa. Faltaba lo que hace falta para facturar: el conteo por mes, el detalle y un archivo para pasarle al cliente o a un sistema contable.

## Decisiones

### 1. Pantalla Facturación, solo para el operador

- `/panel/facturacion`: una fila por empresa activa, con los casos del mes.
- `/panel/facturacion/<empresa>`: el detalle, un renglón por caso resuelto.
- Las dos tienen "Mes anterior" y "Mes siguiente" (`?mes=2026-11`) y "Exportar CSV".
- Un usuario de una empresa recibe 403. Cada empresa ya ve su resumen en Métricas.

### 2. Qué cuenta en cada columna

Cada caso resuelto cae en un solo estado:

| Estado | Regla | ¿Se cobra? |
|---|---|---|
| A cobrar | Lo resolvió el asistente, no se reabrió y ya pasaron 7 días | Sí |
| Dentro de los 7 días | Lo resolvió el asistente hace menos de 7 días | Todavía no |
| Con una persona | Intervino una persona del equipo | No |
| Reabierto | El cliente volvió por el mismo tema dentro de los 7 días | No |

- Aprobar o rechazar un reembolso no es una intervención (decisión 027).
- Un caso reabierto no se cobra aunque después se vuelva a resolver.
- Los casos abiertos, derivados o archivados no aparecen.

### 3. A qué mes pertenece un caso

- **Al mes en que se resolvió**, en hora de Buenos Aires. Un caso resuelto el 31 de octubre a las 23:00 es de octubre, aunque en UTC ya sea noviembre.
- **El mes queda cerrado el día 8 del mes siguiente.** Hasta entonces puede haber casos "dentro de los 7 días", que todavía pueden reabrirse. La pantalla los muestra aparte para que no se facturen antes.
- Si un caso se reabre y se vuelve a resolver en otro mes, pasa a ese mes como "Reabierto". No queda en dos meses.

Opción descartada: asignar el caso al mes en que se cumplen los 7 días. El número de un mes no cambiaría nunca, pero un caso resuelto el 28 aparecería en el mes siguiente, y eso confunde al cliente que mira su factura.

### 4. CSV

- `facturacion-2026-11.csv`: una fila por empresa, con su id (dos empresas pueden llamarse igual).
- `facturacion-<empresa>-2026-11.csv`: una fila por caso, con la columna `se_cobra` (`sí`, `todavía no`, `no`).
- UTF-8 con BOM, para que Excel lo abra con las tildes bien.
- **Sin datos personales:** id del caso, tipo, fechas, quién lo resolvió y estado. Ni mensajes ni teléfonos.

### 5. Consulta entre empresas

- El resumen de todas las empresas es una sola consulta fuera de un tenant, como la lista de Empresas. Solo la alcanza una sesión de operador.
- El detalle de una empresa corre dentro de su `tenant_scope`.

### 6. Sin tabla nueva

Todo sale de `cases` (`status`, `resolved_by`, `reopened`, `closed_at`). No se guarda un cierre de mes: el conteo se calcula al mirarlo.

## Límite conocido

No hay "foto" del mes cerrado. Si alguien corrige un caso viejo en la base, el número de un mes ya facturado cambia. Cuando haya facturas emitidas va a hacer falta guardar el cierre.

## Pendiente

- Precio por caso y mínimo mensual (la spec los deja abiertos): hoy se cuenta, no se calcula un importe.
- Guardar el cierre de cada mes.
- Emitir la factura.
