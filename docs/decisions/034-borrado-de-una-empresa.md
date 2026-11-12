# 034 — Borrado de una empresa

**Estado:** adoptada (12 de noviembre de 2026). Hito 7, tarea 4.

## Contexto

Una empresa que deja de ser cliente, o una creada para una prueba, tiene que poder borrarse entera. Hasta acá no había forma: quedaba pendiente "borrado de una empresa con un comando". La spec no lo define.

## Decisiones

### 1. Desde el panel del operador, con confirmación

- **Empresas → Borrar…** abre una pantalla que muestra qué se va a borrar (casos, conversaciones, documentos, usuarios).
- Para confirmar hay que **escribir el nombre de la empresa**. Sin el nombre exacto no se borra.
- Solo el operador de la plataforma. Un usuario de una empresa recibe 403, también para la suya.
- **La empresa de demo no se borra:** es la que usa el panel del operador.

### 2. Se borra todo, en una sola transacción

- Todas las filas de la empresa en todas las tablas, su fila en `tenants` y sus jobs en la cola.
- También la caché en disco de su onboarding (respuestas del modelo sobre sus datos).
- Es un borrado real, no una marca. El nombre y el email quedan libres para crear la empresa de nuevo.
- Si algo falla, no se borra nada.

### 3. Las tablas se buscan en el catálogo, no están escritas en el código

- El borrado le pregunta a Postgres qué tablas tienen `tenant_id` o una clave foránea a `tenants`, y las ordena según sus claves foráneas.
- **Una tabla agregada por una migración futura se borra sola**, sin tocar este código.
- El test hace lo mismo: recorre todas las tablas con `tenant_id` y todas las columnas `uuid`, y exige cero filas. Si alguien agrega una tabla que el borrado no alcanza, el test falla.

Opción descartada: una lista fija de tablas. Es más simple, pero el día que alguien olvide sumar una tabla nueva quedan datos de una empresa borrada.

### 4. Auditoría del operador

Tabla nueva `operator_audit` (migración 0019): quién, cuándo, qué acción y sobre qué empresa.

- Registra **altas y borrados** de empresas. Se ve en **Empresas → Registro del operador**.
- **Solo cantidades:** filas borradas por tabla. Nunca mensajes, emails ni credenciales.
- **Solo inserción**, también para el dueño de la base: un trigger rechaza `UPDATE` y `DELETE`.
- **No es una tabla de una empresa:** no lleva `tenant_id` ni RLS, porque la empresa borrada ya no existe. La aplicación (`app_user`) no la puede leer ni escribir; solo la conexión dueña, desde el panel del operador.

### 5. La auditoría de casos sigue siendo de solo inserción

`audit_events` no se podía borrar ni siendo el dueño de la base. Eso no cambia, con una sola excepción:

- El borrado marca la transacción con el id de la empresa (`app.deleting_tenant`).
- Con esa marca se pueden borrar eventos **de esa empresa y de ninguna otra**. El control es fila por fila.
- Un `UPDATE`, un `DELETE` sin la marca o de otra empresa fallan como antes. Lo prueba el test.

Opción descartada: conservar la auditoría de la empresa borrada. Tiene los mensajes de sus clientes: no sería un borrado.

### 6. Sesiones

- El panel verifica en cada pedido que la empresa de la sesión exista. Un usuario de una empresa borrada vuelve al login y ya no puede entrar.
- Su chat (`/c/…`) responde 404.

## Límites conocidos

- **Backups:** una copia de respaldo anterior al borrado todavía tiene los datos. Hoy no hay backups propios; los de Neon vencen según su plan.
- **Proveedores externos:** lo que se envió a un modelo o a Resend no se borra desde acá.
- **Logs:** no llevan texto de clientes (decisión 028), pero sí ids de la empresa.
- **Un job en curso** de esa empresa (el análisis del onboarding) puede terminar con error: su empresa ya no está.

## Pendiente

- Exportar los datos de la empresa antes de borrarla.
- Borrado pedido por la propia empresa, con un período de gracia.
- Registrar en `operator_audit` otras acciones del operador (reinicio de la demo).
