# 047 — Ramas del Hito 9

**Contexto.** El Hito 9 son diez tareas, una por pull request a `main`, sin mergear. Varias tocan los mismos archivos (`docs/progreso.md`, el panel, la configuración de la empresa) y algunas usan lo de otra (la bandeja usa el aviso por email; "Go live" usa las reglas).

**Opciones.** (a) Cada rama sale de `main`: PR limpios pero con conflictos al mergear varios. (b) Ramas apiladas, como en el Hito 8 (decisión 036).

**Recomendación (tomada).** (b): cada rama `h9-…` sale de la anterior. Se mergean en orden y sin conflictos; hasta entonces, cada PR muestra también los cambios de las anteriores.
