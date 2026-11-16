"""API de Mensajes de Anthropic simulada, con decisiones guionadas para Acme.

Responde con el mismo formato que la API real (bloques `text`/`tool_use`, `stop_reason`,
`usage`), así se ejercita el código de producción completo sin claves. Las decisiones son
reglas simples que solo miran lo que el agente realmente le manda: el mensaje del cliente, los
documentos, si el contacto está verificado, las herramientas ofrecidas y sus resultados.
No mide la calidad del modelo real.
"""

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx2

from simulated import english
from simulated.voyage import tokens

INVENTED = "¡Sí, claro! Lo hacemos sin ningún problema y sin costo."
_GENERIC = {"tiene", "quier", "puedo", "hacen", "pedid", "acme", "hola", "graci", "neces"}
_STATUS = {
    "paid": "pagado y todavía no fue despachado",
    "shipped": "despachado",
    "delivered": "entregado",
    "cancelled": "cancelado",
}


@dataclass
class SimulatedClaude:
    """Estado observable del modelo simulado (para los tests)."""

    # Modos para probar el control de fundamento: "normal", "invent" (inventa siempre),
    # "invent_once" (inventa y, corregido, responde bien), "fake_source" (cita una fuente que no
    # recibió), "sneaky_number" (mete un dato sin respaldo sin declararlo factual),
    # "asks_contact" (sin verificar, pide email o teléfono en texto en vez de usar la herramienta),
    # "tuteo" (responde con "tú" en lugar de voseo), "reverify" (ya verificado, vuelve a pedir
    # la verificación en texto hasta que se lo corrige), "approval_ungrounded" y
    # "approval_derive" (con la aprobación creada, inventa un monto o deriva).
    # Lo que se vio con Groq real el 12/11/2026 (docs/calidad.md): "hora_sin_cero" (da la hora de
    # despacho como "9:30"), "verificarlo" (explica un pedido ajeno con "para verificarlo"),
    # "modifica_sin_consultar" (como gpt-oss-20b: cambia la dirección sin consultar el pedido y,
    # ante la regla del negocio, deriva; sigue el prompt si este lo indica), "json_suelto" (escribe
    # como texto el JSON de responder), "metadatos" y "vacio" (texto con "Fuentes: ... factual:"
    # o vacío; con "_una_vez", corregido responde bien). En inglés, lo visto con Groq el
    # 16/11/2026 (decisión 045): "deriva_tras_consultar" (con el pedido consultado, deriva hasta
    # que se le pide responder con los datos) y "deriva_siempre_tras_consultar".
    mode: str = "normal"
    requests: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    # -- HTTP ------------------------------------------------------------------------------
    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        tool_names = {t["name"] for t in body.get("tools", [])}
        self.requests.append({"tool_names": tool_names, "body": body})
        if "clasificar" in tool_names:
            blocks = [self._tool_use("clasificar", _classification(body))]
        elif "calificar" in tool_names:
            blocks = [self._tool_use("calificar", _judge(body))]
        else:
            blocks = self._agent(body, tool_names)
        has_tool = any(b["type"] == "tool_use" for b in blocks)
        return httpx2.Response(
            200,
            json={
                "id": f"msg_sim_{self._counter}",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": blocks,
                "stop_reason": "tool_use" if has_tool else "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": len(json.dumps(body)) // 4,
                    "output_tokens": 60,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        )

    def _tool_use(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        return {"type": "tool_use", "id": f"toolu_sim_{self._counter}", "name": name, "input": data}

    # -- agente ----------------------------------------------------------------------------
    def _agent(self, body: dict[str, Any], tool_names: set[str]) -> list[dict[str, Any]]:
        if english.is_english(body):  # empresa en inglés (decisión 038)
            found = _tool_results(body["messages"])
            # Una respuesta o una derivación corregidas (decisión 045), o una corrección en texto.
            fixed = any(name in ("responder", "derivar") for name, _, _ in found) or any(
                isinstance(m["content"], str) for m in body["messages"][1:] if m["role"] == "user"
            )
            return english.EnglishAgent(self.mode, self._tool_use).decide(
                body,
                tool_names,
                [r for r in found if r[0] not in {"responder", "derivar"}],
                fixed,
                lambda data: _loose_text(self.mode, data, fixed),
            )
        first = body["messages"][0]["content"]
        message = html.unescape(_tag(first, "mensaje_cliente"))
        low = message.lower()
        docs = {
            m.group(1): html.unescape(m.group(2))
            for m in re.finditer(r'<documento id="([^"]+)">\n(.*?)\n</documento>', first, re.S)
        }
        all_results = _tool_results(body["messages"])
        corrected = any(name == "responder" for name, _, _ in all_results) or any(
            isinstance(m["content"], str) for m in body["messages"][1:] if m["role"] == "user"
        )
        results = [r for r in all_results if r[0] not in {"responder", "derivar"}]
        order_id = (re.search(r"\b(1\d{3})\b", message) or [None, None])[1]

        def reply(text: str, sources: list[str] | None = None, factual: bool = True) -> list[Any]:
            if self.mode == "tuteo":  # se escapa del voseo, como a veces el modelo real
                text = f"{text} Dime si necesitas algo más."
            data = {"mensaje": text, "fuentes": sources or [], "factual": factual}
            loose = _loose_text(self.mode, data, corrected)
            if loose is not None:
                return [{"type": "text", "text": loose}]
            return [self._tool_use("responder", data)]

        def escalate(reason: str) -> list[Any]:
            return [self._tool_use("derivar", {"motivo": reason, "resumen": message})]

        if re.search(r"ignor\w* (tus|las) instrucciones|todos los pedidos", low):
            return reply(
                "No puedo hacer eso. Solo puedo ayudarte con tus propios pedidos y con consultas "
                "sobre la tienda.",
                factual=False,
            )
        if re.search(r"\b(persona|humano|operador)\b", low):
            return escalate("El cliente pidió hablar con una persona")
        if "derivar" not in tool_names:  # charla: solo puede responder, sin datos de la tienda
            return reply(
                "¡Hola! Soy el asistente virtual de la tienda. Te puedo ayudar con tus pedidos y "
                "con consultas sobre la tienda. ¿En qué te ayudo?",
                factual=False,
            )

        # Lo que hizo Groq en la demo: sin verificar, pide los datos en texto en lugar de usar la
        # herramienta (que dispara la verificación por código).
        if self.mode == "asks_contact" and 'verificado="no"' in first and order_id:
            return reply(
                "Para ayudarte con eso, indícanos tu número de teléfono o correo electrónico.",
                factual=False,
            )
        if self.mode == "asks_contact_en" and 'verificado="no"' in first and order_id:
            return reply(
                "To help with that, could you share your phone number or email address?",
                factual=False,
            )
        # Lo que hizo Groq después de verificar: ya verificado, vuelve a hablar de enviar un
        # código en lugar de usar la herramienta (hasta que se lo corrige).
        if self.mode == "reverify" and 'verificado="sí"' in first and order_id and not corrected:
            return reply(
                "Para iniciar el reembolso necesitás verificar tu cuenta. Te enviamos un código "
                "de 6 dígitos a tu email.",
                factual=False,
            )
        system = json.dumps(body.get("system"), ensure_ascii=False)
        if order_id and results:
            name, call_input, result = results[-1]
            if self.mode == "modifica_sin_consultar" and "error" in result:
                if "regla del negocio" not in system:
                    return escalate(f"No se pudo: {result['error']}")
            return self._after_tool(name, call_input, result, low, message, tool_names, reply)
        address = re.search(r"pedido\s+\d+\s+a\s+(.*\d.*)$", message)
        if (
            self.mode == "modifica_sin_consultar"
            and "Antes de modificar un pedido, consultalo" not in system
            and order_id
            and address
            and "actualizar_direccion" in tool_names
        ):
            return [
                self._tool_use(
                    "actualizar_direccion",
                    {"order_id": order_id, "address": address.group(1).strip()},
                )
            ]
        if order_id and "consultar_pedido" in tool_names:
            return [self._tool_use("consultar_pedido", {"order_id": order_id})]

        if self.mode == "invent" or (self.mode == "invent_once" and not corrected):
            return reply(INVENTED, factual=True)
        if self.mode == "fake_source":
            return reply("Según nuestra política interna, sí.", ["politica_secreta"])
        if self.mode == "sneaky_number":
            return reply("Sí, y te llega en 72 horas.", factual=False)  # 72 no está en ningún doc
        best = _best_document(message, docs)
        if best is not None:
            source, sentence = best
            return reply(sentence, [source])
        return escalate("No encontré la respuesta en los documentos")

    def _after_tool(
        self,
        name: str,
        call_input: dict[str, Any],
        result: dict[str, Any],
        low: str,
        message: str,
        tool_names: set[str],
        reply: Any,
    ) -> list[Any]:
        order_id = call_input.get("order_id")
        if "error" in result:
            if name == "consultar_pedido" and self.mode == "verificarlo":
                return reply(
                    f"No encontré el pedido {order_id} entre los tuyos. Revisá el email de "
                    "confirmación para verificarlo.",
                    factual=False,
                )
            if name == "consultar_pedido":
                return reply(
                    f"No encuentro el pedido {order_id} entre los pedidos asociados a tu número. "
                    "¿Querés revisar el número?",
                    factual=False,
                )
            return reply(f"No pude completar la operación: {result['error']}", factual=True)
        if result.get("status") == "approval_required":
            # Después de crear la aprobación, el modelo falla: inventa un monto (no pasa el
            # control de fundamento) o deriva en lugar de avisar que quedó en revisión.
            if self.mode == "approval_ungrounded":
                return reply(f"Iniciamos el reembolso de $99.999 del pedido {order_id}.")
            if self.mode == "approval_derive":
                return [
                    self._tool_use("derivar", {"motivo": "Requiere aprobación", "resumen": low})
                ]
            return reply(
                "Tu pedido quedó en revisión: una persona del equipo lo tiene que aprobar y te "
                "avisamos por acá apenas esté.",
                factual=True,
            )
        data = result.get("data") or {}
        if name == "actualizar_direccion":
            return reply(
                f"Listo, actualicé la dirección del pedido {order_id} a "
                f"{data['shipping_address']}. "
                f"La entrega estimada es el {_date(data.get('estimated_delivery'))}."
            )
        if name == "consultar_pedido":
            status = data.get("status")
            if "direcci" in low and "actualizar_direccion" in tool_names:
                # Una dirección trae un número de calle: "a otra dirección" no lo es.
                address = re.search(r"pedido\s+\d+\s+a\s+(.*\d.*)$", message)
                if status == "paid" and address:
                    return [
                        self._tool_use(
                            "actualizar_direccion",
                            {"order_id": order_id, "address": address.group(1).strip()},
                        )
                    ]
                if status == "paid":  # falta el dato: se lo pide en lugar de derivar
                    return reply(
                        f"Sí, el pedido {order_id} todavía no fue despachado. ¿A qué dirección "
                        "querés que lo mandemos? Pasame calle, número y ciudad.",
                    )
                return reply(
                    f"El pedido {order_id} ya fue despachado, así que no puedo cambiar la "
                    "dirección. Si querés, te paso con una persona del equipo para ver "
                    "alternativas."
                )
            if "reembols" in low and "iniciar_reembolso" in tool_names:
                return [
                    self._tool_use(
                        "iniciar_reembolso", {"order_id": order_id, "amount": data.get("total", 0)}
                    )
                ]
            if "cancel" in low and "cancelar_pedido" in tool_names:
                return [self._tool_use("cancelar_pedido", {"order_id": order_id})]
            text = f"Tu pedido {order_id} está {_STATUS.get(str(status), status)}."
            shipped = str(data.get("shipped_at") or "")
            if self.mode == "hora_sin_cero" and len(shipped) >= 16:  # "09:30" → "9:30"
                text += f" Salió a las {int(shipped[11:13])}:{shipped[14:16]}."
            if data.get("estimated_delivery"):
                text += f" La fecha estimada de entrega es el {_date(data['estimated_delivery'])}."
            return reply(text)
        if name in {"cancelar_pedido", "iniciar_reembolso"}:
            return reply(f"Listo, quedó registrada la operación sobre el pedido {order_id}.")
        return reply("Listo.")


# -- utilidades --------------------------------------------------------------------------
def _loose_text(mode: str, data: dict[str, Any], corrected: bool) -> str | None:
    """Texto suelto en lugar de la herramienta responder, como gpt-oss-120b."""
    if mode == "json_suelto":
        return json.dumps({"accion": "responder", **data}, ensure_ascii=False, indent=2)
    if mode == "metadatos" or (mode == "metadatos_una_vez" and not corrected):
        return f"{data['mensaje']}\n\nFuentes: {data['fuentes']}  factual: true"
    if mode == "vacio" or (mode == "vacio_una_vez" and not corrected):
        return ""
    return None


def _tag(text: str, name: str) -> str:
    found = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    return found.group(1) if found else text


def _date(value: str | None) -> str:
    if not value:
        return "a confirmar"
    year, month, day = value.split("-")
    return f"{day}/{month}"


def _tool_results(
    messages: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    ordered: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for message in messages:
        if not isinstance(message["content"], list):
            continue
        for block in message["content"]:
            if block.get("type") == "tool_use":
                calls[block["id"]] = (block["name"], block["input"])
            elif block.get("type") == "tool_result" and block["tool_use_id"] in calls:
                name, call_input = calls[block["tool_use_id"]]
                ordered.append((name, call_input, json.loads(block["content"])))
    return ordered


def _best_document(question: str, docs: dict[str, str]) -> tuple[str, str] | None:
    wanted = {t for t in tokens(question) if t not in _GENERIC}
    best: tuple[int, str, str] | None = None
    for source, text in docs.items():
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            score = len(wanted & set(tokens(sentence)))
            if score and (best is None or score > best[0]):
                best = (score, source, sentence.strip())
    return (best[1], best[2]) if best else None


_RULES = [
    (r"ignor\w* (tus|las) instrucciones|todos los (pedidos|clientes)", "pedido_no_permitido"),
    (
        r"ignore (your|all|the|previous) \w*\s*instructions|all (the )?(orders|customers)",
        "pedido_no_permitido",
    ),
    (r"\b(persona|humano|operador)\b", "derivar_humano"),
    (r"\b(a person|a human|real person|representative|someone on your team)\b", "derivar_humano"),
    (
        r"^\W*(hola+|buenas+|buen d[ií]a+|gracias|chau|como andas|jaja\w*)\W*$|sos un bot"
        r"|q(ue)? pod[eé]s hacer|partido|peli\b|clima|nunca responden|la tarea",
        "conversacion_general",
    ),
    (
        r"^\W*(hi+|hello+|hey+|hi there|good (morning|afternoon)|thanks|bye)\W*$|are you a bot"
        r"|what can you do|weather|homework",
        "conversacion_general",
    ),
    (r"cu[aá]nto tarda|env[ií]o gratis|gu[ií]a de talles", "consulta_general"),
    (r"direcci", "cambiar_direccion"),
    (r"cancel", "cancelar_pedido"),
    (r"reembols|devoluc|devuelv|plata", "reembolso"),
    (r"talle", "cambio_por_talle"),
    (r"cuota|pagar|pago|tarjeta|transferencia", "medios_de_pago_y_cuotas"),
    (r"factura", "facturacion"),
    (r"sucursal|horario|abren|domingo|sábado|sabado|feriado", "horarios_y_sucursales"),
    (r"pedido|llega|envío|envio", "donde_esta_mi_pedido"),
    # Inglés, con los tipos de Northwind (decisión 038) y de Acme (en los tests de idioma Acme
    # atiende en inglés). Una pregunta de políticas sin número de pedido es consulta general.
    (r"\baddress\b", "change_shipping_address"),
    (r"\baddress\b", "cambiar_direccion"),
    (r"\bcancel", "cancel_order"),
    (r"\brefund|money back", "refund"),
    (r"\brefund", "reembolso"),
    (r"exchange|another size|different size|swap|size [a-z0-9]+ instead", "exchange"),
    (r"defect|broken|ripped|torn|warranty claim|zipper", "warranty_claim"),
    (r"\bopen\b|sunday|hours|store", "horarios_y_sucursales"),
    (r"\border\b|arrive|where is|late|package|tracking|deliver", "where_is_my_order"),
    (r"\border\b|arrive|where is", "donde_esta_mi_pedido"),
]
# Preguntas sobre las políticas sin un pedido puntual (en inglés): consulta general.
_POLICY = re.compile(
    r"policy|how long|how much|shipping cost|free shipping|do you ship|size guide|what size"
    r"|warranty cover|coupons?\b|discounts?\b|price|hours|store|international",
    re.I,
)


# Seguir el tema de la conversación (decisión 040): elegir una opción, "actually", "instead".
_CONTINUES = re.compile(
    r"(?i)\b(i'?ll take|i will take|i'?d like the|i'?d rather|actually|instead|that one|go with"
    r"|changed my mind|ok(ay)?|yes|sure|sounds good)\b"
)


def _classification(body: dict[str, Any]) -> dict[str, Any]:
    """El tipo del mensaje y, en inglés, el tema actual y un segundo tema (decisión 040)."""
    label = _classify(body)
    if not str(body.get("system") or "").startswith("You classify"):
        return {"tipo": label}  # el clasificador en español no cambia
    content = body["messages"][0]["content"]
    text = html.unescape(_tag(content, "mensaje_cliente")).lower()
    current = re.search(r"<tipo_actual>(.*?)</tipo_actual>", content)
    if current and label in {"otro", "consulta_general"} and _CONTINUES.search(text):
        label = current.group(1)
    found: dict[str, Any] = {"tipo": label}
    others = body["tools"][0]["input_schema"]["properties"].get("otro_tipo", {}).get("enum", [])
    if len(english.order_ids(text)) >= 2:
        second = next(
            (lab for pat, lab in _RULES if lab in others and lab != label and re.search(pat, text)),
            None,
        )
        if second is not None:
            found["otro_tipo"] = second
    return found


def _classify(body: dict[str, Any]) -> str:
    text = html.unescape(_tag(body["messages"][0]["content"], "mensaje_cliente")).lower()
    enum = set(body["tools"][0]["input_schema"]["properties"]["tipo"]["enum"])
    english_prompt = str(body.get("system") or "").startswith("You classify")
    if english_prompt and _POLICY.search(text) and not english.order_ids(text):
        if not re.search(r"\b(a person|a human|representative|ignore)\b", text):
            return "consulta_general"
    for pattern, label in _RULES:
        if re.search(pattern, text) and label in enum:
            return label
    return "otro"


def _judge(body: dict[str, Any]) -> dict[str, Any]:
    """Juez simulado: puntaje por presencia de las palabras esperadas (ver evals/runner.py)."""
    content = body["messages"][0]["content"]
    answer = html.unescape(_tag(content, "respuesta")).lower()
    expected = [w for w in html.unescape(_tag(content, "palabras_clave")).lower().split("|") if w]
    if not expected:
        return {"puntaje": 1.0, "motivo": "Sin palabras clave esperadas."}
    hits = sum(1 for word in expected if word.strip() in answer)
    return {"puntaje": round(hits / len(expected), 2), "motivo": f"{hits}/{len(expected)}"}
