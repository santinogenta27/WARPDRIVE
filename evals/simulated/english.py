"""El agente simulado en inglés (decisión 038): decisiones guionadas para Northwind Outfitters (y
para Acme cuando atiende en inglés en los tests de idioma).

Igual que el simulado en español, solo mira lo que el agente le manda (el mensaje, el
historial, los documentos, las herramientas ofrecidas y sus resultados) y responde con el
formato de la API real. No mide la calidad del modelo real.
"""

import html
import re
from collections.abc import Callable
from typing import Any

ENGLISH_PROMPT = "You are the support assistant"
_ORDER = re.compile(r"(?<![\w-])(?:#\s*)?(NW-?\s?\d{4}|1\d{3})(?!\d)", re.I)
_UPSET = re.compile(
    r"(?i)\b(?:ridiculous|unacceptable|frustrat\w*|angry|upset|annoyed|terrible|worst|furious"
    r"|disappointed|still (?:hasn'?t|not)|where the hell|fed up|seriously)\b|!!|\?!"
)
_EXCHANGE = re.compile(r"exchange|swap|another size|different size|bigger|smaller", re.I)
_PERCENT = re.compile(r"(\d{1,3})\s*%")
_SIZE = re.compile(r"\b(?:size|an?|in)\s+(XS|XL|S|M|L|\d{1,2})\b(?!')", re.I)
_MONTHS = "January February March April May June July August September October November December"
_STOP = {"what", "your", "the", "and", "for", "you", "can", "how", "does", "with", "this"}

Reply = Callable[..., list[dict[str, Any]]]
ToolUse = Callable[[str, dict[str, Any]], dict[str, Any]]


def is_english(body: dict[str, Any]) -> bool:
    return str(body.get("system") or "").startswith(ENGLISH_PROMPT)


def order_ids(text: str) -> list[str]:
    """Los números de pedido del texto, como los escribe la tienda: "NW-4521" o "1001"."""
    found: list[str] = []
    for raw in _ORDER.findall(text):
        digits = re.sub(r"\D", "", raw)
        order = f"NW-{digits}" if raw.upper().startswith("NW") else digits
        if order not in found:
            found.append(order)
    return found


def long_date(value: str | None) -> str:
    """ "2026-11-14" -> "November 14"."""
    if not value:
        return "to be confirmed"
    _, month, day = value[:10].split("-")
    return f"{_MONTHS.split()[int(month) - 1]} {int(day)}"


def money(value: Any) -> str:
    amount = float(value or 0)
    return f"${amount:,.2f}".removesuffix(".00")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def best_sentence(question: str, docs: dict[str, str]) -> tuple[str, str] | None:
    wanted = {w[:5] for w in _words(question)}
    best: tuple[int, str, str] | None = None
    for source, text in docs.items():
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip().lstrip("-# ").strip()
            if len(sentence.split()) < 6:  # el título del documento no es una respuesta
                continue
            score = len(wanted & {w[:5] for w in _words(sentence)})
            if score and (best is None or score > best[0]):
                best = (score, source, sentence)
    return (best[1], best[2]) if best else None


def _tag(text: str, name: str) -> str:
    found = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    return found.group(1) if found else ""


class EnglishAgent:
    def __init__(self, mode: str, tool_use: ToolUse) -> None:
        self.mode = mode
        self.tool_use = tool_use

    def decide(
        self,
        body: dict[str, Any],
        tool_names: set[str],
        results: list[tuple[str, dict[str, Any], dict[str, Any]]],
        corrected: bool,
        loose: Callable[[dict[str, Any]], str | None],
    ) -> list[dict[str, Any]]:
        first = body["messages"][0]["content"]
        message = html.unescape(_tag(first, "mensaje_cliente") or first)
        history = html.unescape(_tag(first, "historial"))
        low = message.lower()
        docs: dict[str, str] = {}  # un documento puede llegar en varios fragmentos
        for m in re.finditer(r'<documento id="([^"]+)">\n(.*?)\n</documento>', first, re.S):
            docs[m.group(1)] = f"{docs.get(m.group(1), '')}\n{html.unescape(m.group(2))}"
        orders = order_ids(message) or order_ids(history)[-1:]
        order_id = orders[0] if orders else None
        upset = bool(_UPSET.search(message))

        def reply(text: str, sources: list[str] | None = None, factual: bool = True) -> list[Any]:
            if self.mode == "tuteo":  # el simulado en español se escapa al tuteo: acá no se corrige
                text = f"{text} Dime si necesitas algo más."
            data = {"mensaje": text, "fuentes": sources or [], "factual": factual}
            text_only = loose(data)
            if text_only is not None:
                return [{"type": "text", "text": text_only}]
            return [self.tool_use("responder", data)]

        def escalate(reason: str) -> list[Any]:
            return [self.tool_use("derivar", {"motivo": reason, "resumen": message})]

        if re.search(r"ignore (your|all|the|previous)\b.*instructions|all (the )?orders", low):
            return reply(
                "I can't do that. I can help with your own orders and any question about the "
                "store.",
                factual=False,
            )
        if re.search(r"\b(a person|a human|real person|representative|agent)\b", low):
            return escalate("The customer asked to talk to a person.")
        if "derivar" not in tool_names:  # charla: sin datos de la tienda
            if upset:
                return reply(
                    "I'm really sorry for the frustration. I'm here to help: tell me your order "
                    "number and I'll look into it right away.",
                    factual=False,
                )
            return reply(
                "Hi! I'm the store's virtual assistant. I can help with your orders and any "
                "question about the store. How can I help?",
                factual=False,
            )
        if self.mode == "asks_contact_en" and 'verificado="no"' in first and order_id:
            return reply(
                "To help with that, could you share your phone number or email address?",
                factual=False,
            )
        if results:
            name, call_input, result = results[-1]
            return self.after_tool(name, call_input, result, low, message, tool_names, reply)
        if order_id and "consultar_pedido" in tool_names:
            return [self.tool_use("consultar_pedido", {"order_id": order_id})]
        best = best_sentence(message, docs)
        if best is not None:
            source, sentence = best
            return reply(sentence, [source])
        return escalate("I couldn't find the answer in the documents.")

    def after_tool(
        self,
        name: str,
        call_input: dict[str, Any],
        result: dict[str, Any],
        low: str,
        message: str,
        tool_names: set[str],
        reply: Reply,
    ) -> list[dict[str, Any]]:
        order_id = call_input.get("order_id")
        if "alternativas" in result:  # el guardián rechazó algo fuera de la política
            return reply(self.allowed_instead(call_input, result))
        if "error" in result:
            if name == "consultar_pedido":
                return reply(
                    f"I couldn't find order {order_id} among your orders. Could you double-check "
                    "the number?",
                    factual=False,
                )
            return reply(f"I couldn't complete that: {result['error']}", factual=True)
        if result.get("status") == "approval_required":
            amount = call_input.get("amount")
            what = (
                f"Your refund of {money(amount)} for order {order_id}"
                if amount
                else (f"Your request for order {order_id}")
            )
            return reply(
                f"{what} is now under review: a member of our team needs to approve it, and "
                "you'll get a message right here as soon as it's done."
            )
        data = result.get("data") or {}
        if name == "actualizar_direccion":
            return reply(
                f"Done! Order {order_id} will now ship to {data['shipping_address']}. The "
                f"estimated delivery date is {long_date(data.get('estimated_delivery'))}."
            )
        if name == "cancelar_pedido":
            return reply(f"Done! Order {order_id} is cancelled and the payment is released.")
        if name == "iniciar_reembolso":
            return reply(
                f"Done! I started a refund of {money(data.get('amount'))} for order {order_id}. "
                "It goes back to your original payment method."
            )
        if name in self.DONE:
            return reply(self.DONE[name](data))
        if name != "consultar_pedido":
            return reply("Done.")
        status = str(data.get("status"))
        if "address" in low and "actualizar_direccion" in tool_names:
            address = re.search(r"\bto\s+(\d+.*)$", message)
            if status == "paid" and address:
                return [
                    self.tool_use(
                        "actualizar_direccion",
                        {"order_id": order_id, "address": address.group(1).strip(" .")},
                    )
                ]
            if status == "paid":
                return reply(
                    f"Order {order_id} hasn't shipped yet, so I can change the address. What's "
                    "the new address, including the city, state and ZIP code?"
                )
            return reply(
                f"Order {order_id} has already shipped, so I can't change its address anymore. "
                "If you'd like, I can ask a member of our team to review it with the carrier."
            )
        if "refund" in low and "iniciar_reembolso" in tool_names:
            if status == "delivered":
                return [
                    self.tool_use(
                        "iniciar_reembolso", {"order_id": order_id, "amount": data.get("total")}
                    )
                ]
            return reply(
                f"Order {order_id} hasn't been delivered yet, so it can't be refunded right now. "
                "As soon as it arrives you can return it for a full refund."
            )
        if "cancel" in low and "cancelar_pedido" in tool_names:
            if status == "paid":
                return [self.tool_use("cancelar_pedido", {"order_id": order_id})]
            return reply(
                f"Order {order_id} has already shipped, so it can't be cancelled. Once it "
                "arrives you can return it for free."
            )
        if _EXCHANGE.search(low) and "iniciar_cambio" in tool_names:
            return self.exchange(order_id, data, message, reply)
        percent = _PERCENT.search(message)
        if percent and "ofrecer_cupon" in tool_names:
            call = {"order_id": order_id, "percent": int(percent.group(1))}
            return [self.tool_use("ofrecer_cupon", call)]
        if "express" in low and "ofrecer_envio_express" in tool_names:
            return [self.tool_use("ofrecer_envio_express", {"order_id": order_id})]
        return reply(self.order_status(order_id, data))

    def exchange(
        self, order_id: Any, data: dict[str, Any], message: str, reply: Reply
    ) -> list[dict[str, Any]]:
        items = data.get("items") or []
        if data.get("status") != "delivered" or not items:
            return reply(
                f"Order {order_id} hasn't been delivered yet. Once it arrives, you have 30 days "
                "to exchange it for free."
            )
        size = _SIZE.search(message)
        if size is None:
            return reply(
                f"Sure! Which size would you like instead of {items[0]['size']} for your "
                f"{items[0]['name']}?",
                factual=False,
            )
        call = {"order_id": order_id, "item_sku": items[0]["sku"], "new_size": size.group(1)}
        return [self.tool_use("iniciar_cambio", {**call, "new_size": call["new_size"].upper()})]

    @staticmethod
    def allowed_instead(call_input: dict[str, Any], result: dict[str, Any]) -> str:
        """Lo que la política permite, cuando el guardián rechazó un pedido fuera de ella."""
        top = result.get("maximo_permitido")
        others = {a.get("herramienta") for a in result.get("alternativas") or []}
        asked = call_input.get("percent")
        options = [f"a {top}% coupon for your next order"]
        if "ofrecer_envio_express" in others:
            options.append("free express shipping on your next order")
        return (
            f"I'm sorry, I can't offer {asked}% off: our courtesy coupons go up to {top}%. "
            f"What I can do is give you {' or '.join(options)}. Which would you prefer?"
        )

    @staticmethod
    def _exchanged(data: dict[str, Any]) -> str:
        before, after = data["from_item"], data["to_item"]
        new = f"size {after['size']}" if after["name"] == before["name"] else after["name"]
        if after["name"] != before["name"]:
            new += f" (size {after['size']})"
        text = (
            f"Done! I started the exchange of your {before['name']} (size {before['size']}) for "
            f"{new}. We emailed you a prepaid return label, and the new item ships as soon as "
            "the carrier scans your return."
        )
        difference = float(data.get("price_difference") or 0)
        if difference > 0:
            text += f" The new item costs {money(difference)} more."
        elif difference < 0:
            text += f" We'll refund you the difference of {money(-difference)}."
        return text

    DONE: dict[str, Callable[[dict[str, Any]], str]] = {
        "iniciar_cambio": _exchanged,
        "ofrecer_cupon": lambda data: (
            f"Done! Here's your coupon {data['code']}: {data['percent']}% off your next order, "
            f"valid for {data['valid_days']} days."
        ),
        "ofrecer_envio_express": lambda data: (
            f"Done! Your next order ships express for free with the code {data['code']}, valid "
            f"for {data['valid_days']} days."
        ),
    }

    @staticmethod
    def order_status(order_id: Any, data: dict[str, Any]) -> str:
        items = ", ".join(i["name"] for i in data.get("items") or []) or "your items"
        status = str(data.get("status"))
        if status == "delivered":
            return f"Your order {order_id} ({items}) was delivered."
        if status == "paid":
            return (
                f"Your order {order_id} ({items}) hasn't shipped yet. The estimated delivery "
                f"date is {long_date(data.get('estimated_delivery'))}."
            )
        if status == "cancelled":
            return f"Your order {order_id} ({items}) was cancelled."
        carrier = f" with {data['carrier']}" if data.get("carrier") else ""
        text = f"Your order {order_id} ({items}) is on its way{carrier}"
        if data.get("tracking_number"):
            text += f", tracking number {data['tracking_number']}"
        delay = data.get("delay")
        if delay:
            return (
                f"{text}. It's running late: {str(delay['reason']).lower()}. The new estimated "
                f"delivery date is {long_date(delay['new_estimated_delivery'])}."
            )
        return f"{text}. It should arrive on {long_date(data.get('estimated_delivery'))}."
