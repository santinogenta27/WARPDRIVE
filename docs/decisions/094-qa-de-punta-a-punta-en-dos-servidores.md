# 094 — Dónde correr el QA de punta a punta (etapa 2)

**Contexto.** La etapa 2 del Hito QA 2 pide un cliente nuevo de punta a punta contra el servidor de
prueba real (`https://warpdrive-5vui.onrender.com`). Tiene que verse en Home, Inbox, Customers e
Insights. Pero Render despliega `main` (`render.yaml`, `branch: main`), y `main` todavía tiene el
panel del Hito 12: no tiene Home, Knowledge, Deploy ni la navegación del Hito 13. Tampoco tiene los
arreglos de este QA. El hito pide no mergear nada en `main`, y cambiar la rama del servicio de
Render solo se puede desde su panel, con la cuenta del dueño.

**Opciones.**
1. Correr todo contra Render. Las pantallas del Hito 13 no existen ahí, así que la mitad del
   recorrido no se puede probar.
2. Correr todo localmente. No prueba el servidor real: proxy, HTTPS, variables de Render y email.
3. Las dos cosas. Contra Render, lo que `main` tiene: registro, confirmación del email, panel,
   carga de documentos, widget en una página de prueba y conversaciones. El recorrido entero del
   Hito 13 (Knowledge, Agent, Deploy, Inbox, Customers, Home e Insights), contra `hito-13`
   levantado como servidor HTTP real (uvicorn) con la base de staging, por HTTP y con un navegador
   headless.

**Recomendación (aplicada): 3.** Queda como pendiente repetir el recorrido en Render cuando
`hito-13` llegue a `main`: el PR de `hito-13` contra `main` queda abierto, sin mergear.

**Confirmación del email en Render.** Si Render no tiene `RESEND_API_KEY`, el email no sale y el
código va al log del servidor, que solo se ve con la cuenta del dueño. En ese caso el QA confirma
con un token propio. El registro solo guarda el hash del enlace, así que se reemplaza ese hash en
la base de la demo por el de un token conocido y se abre el mismo enlace
`/registro/confirmar?token=…`. Así se prueba todo el camino menos la entrega del email.
