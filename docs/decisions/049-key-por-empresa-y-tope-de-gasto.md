# 049 — API key por empresa y tope de gasto

**Contexto.** El Hito 9 pide que cada empresa pueda usar su propia API key (Anthropic, OpenAI, Groq o Gemini), guardada cifrada; sin key, la de la plataforma con límites. Tope de gasto mensual con aviso al 80 % y derivación al 100 %.

**Qué se decidió.**

- **Dónde:** `tenants.settings.llm` guarda el proveedor y la key cifrada con la clave de la empresa (`encrypt_for`, como las credenciales del sistema). Sin migración. El panel nunca muestra la key.
- **OpenAI** usa el mismo transporte que Groq (la API de chat completions es la misma), con otra URL. Modelo `gpt-5-mini` o `OPENAI_AGENT_MODEL`. **Sus precios en `pricing.py` hay que verificarlos** antes de facturar.
- **No se prueba la key al guardarla:** probarla gasta una llamada y la cuota de la empresa. Una key inválida se ve en el primer mensaje, como error en Métricas.
- **Límites con la key de la plataforma:**
  - tope en USD: el de la empresa o `SHARED_MONTHLY_USD` (5 por defecto), el menor;
  - `SHARED_MONTHLY_CALLS` llamadas al modelo por mes (2000 por defecto), porque Groq y Gemini gratuitos cuestan 0 y el límite real es la cuota.
  - Las empresas de demo (Acme y Northwind) no tienen límite de llamadas, así la demo no se corta.
- **Con key propia:** solo el tope que fije la empresa; sin tope si no fija ninguno.
- **Al 100 %:** cada mensaje se deriva a una persona sin llamar al modelo (tampoco el clasificador), con la causa `tope_de_gasto` en la fila Why.
- **Avisos:** al cruzar el 80 % y el 100 %, una alerta en los logs y por email al operador (`ALERT_EMAIL_TO` o `PANEL_EMAIL`), como las de la decisión 033. Al equipo de la empresa le llegará con la bandeja (tarea 7).
- **El mes** es el de Buenos Aires, como la facturación (decisión 032).
- **Métricas** (resueltos, derivados, CSAT y costo) ya estaban en la página Métricas (decisiones 028 y 033). Configuración suma el gasto del mes contra el tope.
