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
CANCEL = "cancelar_pendiente"  # herramienta interna del cambio de idea (decisión 040)
_PENDING_NAMES = {"iniciar_reembolso": "refund", "cancelar_pedido": "cancellation"}
_CHANGED = re.compile(r"changed my mind|change of plans|instead|rather|actually", re.I)
_EMPATHY = re.compile(r"\b(sorry|apologi|understand)", re.I)
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


PRODUCTS = "buscar_productos"  # búsqueda de productos (decisión 059)
_SHOP_STOP = _STOP | {"have", "sell", "carry", "much", "size", "need", "want", "looking", "like"}


CHECKOUT = "armar_carrito"  # link de compra (decisión 060)
_BUY = re.compile(
    r"(?i)\b(i'?ll take|i will take|buy|purchase|send me the link|checkout|add (it )?to (my )?cart"
    r"|order it|i want it)\b"
)


OFFER = "ofrecer_producto"  # venta cruzada (decisión 061)


def checkout_text(data: dict[str, Any]) -> str:
    line = (data.get("items") or [{}])[0]
    return (
        f"Here's your link to buy the {line.get('name')} ({line.get('color')}, size "
        f"{line.get('size')}) for {money(data.get('total'))}: {data.get('url')} You complete "
        "the payment on our store's checkout."
    )


def offer_call(body: dict[str, Any], mode: str) -> dict[str, Any] | None:
    """El producto de la herramienta de oferta y su descuento máximo (de la descripción). En
    el modo "descuento_alto", pide el doble del máximo (el guardián lo rechaza)."""
    tool = next((t for t in body.get("tools") or [] if t["name"] == OFFER), None)
    if tool is None:
        return None
    names = tool["input_schema"]["properties"]["product"]["enum"]
    top = re.search(r"up to (\d+(?:\.\d+)?)% off", tool["description"])
    discount = float(top.group(1)) if top else 0.0
    return {
        "product": names[0],
        "discount_percent": discount * 2 if mode == "descuento_alto" else discount,
    }


def with_offer(results: list[tuple[str, dict[str, Any], dict[str, Any]]]) -> str:
    """El link de compra y, si el guardián la dejó, la oferta con su precio real."""
    checkout = next(r[2].get("data") or {} for r in reversed(results) if r[0] == CHECKOUT)
    text = checkout_text(checkout)
    offer = results[-1][2].get("data")
    if offer:
        product = offer["product"]
        discount = offer.get("discount_percent") or 0
        deal = f" and I can give you {discount:g}% off" if discount else ""
        text += (
            f" By the way, our {product['name']} is {money(product['price'])}{deal}, in case "
            "you'd like to add it."
        )
    return text


def wants_to_buy(message: str) -> bool:
    return _BUY.search(message) is not None


def choose_variant(data: dict[str, Any], message: str) -> str | None:
    """El SKU de la variante con stock que pide el cliente (por su color) o la primera."""
    low = message.lower()
    available = [
        v
        for product in data.get("products") or []
        for v in product.get("variants") or []
        if v.get("stock") is None or v["stock"] > 0
    ]
    named = [v for v in available if str(v.get("color", "")).lower() in low]
    picked = (named or available or [None])[0]
    return str(picked["sku"]) if picked else None


def product_search(message: str) -> dict[str, Any]:
    """Lo que busca el cliente y su talle: "a rain jacket in size M" -> rain jacket, M."""
    size = _SIZE.search(message)
    text = _SIZE.sub(" ", message) if size else message
    words = [w.rstrip("s") for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _SHOP_STOP]
    found: dict[str, Any] = {"query": " ".join(words)}
    if size:
        found["size"] = size.group(1).upper()
    return found


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
        # Lo que se canceló en este turno porque el cliente cambió de idea (decisión 040).
        cancelled = [
            item
            for name, _, result in results
            if name == CANCEL
            for item in result.get("canceladas") or []
        ]

        def reply(text: str, sources: list[str] | None = None, factual: bool = True) -> list[Any]:
            if self.mode == "tuteo":  # el simulado en español se escapa al tuteo: acá no se corrige
                text = f"{text} Dime si necesitas algo más."
            for item in cancelled:
                what = _PENDING_NAMES.get(item["herramienta"], "request")
                order = (item.get("parametros") or {}).get("order_id")
                text = f"I cancelled your {what} request for order {order}. {text}"
            # Con el cliente molesto, primero la empatía (en el modo "sin_empatia", recién
            # cuando el control en código lo corrige).
            if upset and not _EMPATHY.search(text) and (self.mode != "sin_empatia" or corrected):
                text = f"I'm really sorry about the trouble. {text}"
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
        if self.mode == "afirma_sin_hacer" and not corrected and not results:
            # Como gpt-oss-20b en la prueba de la demo: afirma algo que no hizo.
            return reply(
                "Done! I've added free express shipping to your next order.", factual=False
            )
        # Cambio de idea: primero se cancela lo pendiente; después, lo nuevo.
        if (
            CANCEL in tool_names
            and _CHANGED.search(low)
            and not any(r[0] == CANCEL for r in results)
        ):
            return [self.tool_use(CANCEL, {"order_id": order_id} if order_id else {})]
        if results and results[-1][0] == CANCEL:
            denied = next((r for r in reversed(results) if "pendiente" in r[2]), None)
            if denied is not None:
                return [self.tool_use(denied[0], denied[1])]
            if order_id and "consultar_pedido" in tool_names:
                return [self.tool_use("consultar_pedido", {"order_id": order_id})]
        # Dos temas: cada pedido con lo suyo, en una sola respuesta.
        if len(order_ids(message)) >= 2 and "consultar_pedido" in tool_names:
            return self.several(message, results, tool_names, reply)
        # Como gpt-oss-20b en Render el 16/11/2026 (decisión 045): con el pedido ya consultado,
        # deriva. "deriva_tras_consultar": hasta que se le pide responder con los datos;
        # "deriva_siempre_tras_consultar": siempre, haya salido bien la consulta o no.
        if results and results[-1][0] == "consultar_pedido" and self.mode.startswith("deriva_"):
            looked_up = "error" not in results[-1][2]
            if self.mode == "deriva_siempre_tras_consultar" or (looked_up and not corrected):
                return escalate("Customer request for order status")
        # Venta cruzada (decisión 061): con el link ya armado, una oferta si la herramienta está.
        if results and results[-1][0] == CHECKOUT and "error" not in results[-1][2]:
            offer = offer_call(body, self.mode)
            if offer is not None and OFFER in tool_names:
                return [self.tool_use(OFFER, offer)]
        if results and results[-1][0] == OFFER:
            top = results[-1][2].get("maximo_permitido")
            if top is not None and not any(r[1].get("discount_percent") == top for r in results):
                return [self.tool_use(OFFER, {**results[-1][1], "discount_percent": top})]
            return reply(with_offer(results))
        if results:
            name, call_input, result = results[-1]
            if "pendiente" in result and CANCEL in tool_names:
                return [self.tool_use(CANCEL, {"order_id": call_input.get("order_id")})]
            return self.after_tool(
                name, call_input, result, low, message, tool_names, reply, corrected
            )
        if order_id and "consultar_pedido" in tool_names:
            return [self.tool_use("consultar_pedido", {"order_id": order_id})]
        if PRODUCTS in tool_names:  # preventa (decisión 059): primero la búsqueda
            # "I'll take the black one": el producto y el talle están en la conversación.
            asked = f"{history} {message}" if wants_to_buy(message) else message
            return [self.tool_use(PRODUCTS, product_search(asked))]
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
        corrected: bool = False,
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
        if name == PRODUCTS and wants_to_buy(message) and CHECKOUT in tool_names:
            chosen = choose_variant(data, message)
            if chosen is not None:
                return [self.tool_use(CHECKOUT, {"items": [{"sku": chosen, "quantity": 1}]})]
        if name == PRODUCTS:
            return reply(self.recommend(call_input, data, message, corrected))
        if name == CHECKOUT:
            return reply(checkout_text(data))
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
        offers = bool({"ofrecer_cupon", "ofrecer_envio_express"} & tool_names)
        return reply(self.order_status(order_id, data, offers))

    def several(
        self,
        message: str,
        results: list[tuple[str, dict[str, Any], dict[str, Any]]],
        tool_names: set[str],
        reply: Reply,
    ) -> list[dict[str, Any]]:
        """Un mensaje con dos pedidos y dos temas: consulta cada pedido, hace lo que pide cada
        frase (el cambio de talle) y responde todo junto."""
        looked = {
            r[1].get("order_id"): r[2].get("data") or {}
            for r in results
            if r[0] == "consultar_pedido"
        }
        wanted = order_ids(message)
        for order in wanted:
            if order not in looked:
                return [self.tool_use("consultar_pedido", {"order_id": order})]
        sentences = re.split(r"(?<=[.?!])\s+", message)
        offers = bool({"ofrecer_cupon", "ofrecer_envio_express"} & tool_names)
        parts: list[str] = []
        for order in wanted:
            data = looked[order]
            sentence = next((s for s in sentences if order in s.upper()), "")
            if not data:
                parts.append(f"I couldn't find order {order} among your orders.")
                continue
            if _EXCHANGE.search(sentence) and "iniciar_cambio" in tool_names:
                done = next(
                    (
                        r
                        for r in results
                        if r[0] == "iniciar_cambio" and r[1].get("order_id") == order
                    ),
                    None,
                )
                if done is None:
                    asked = self.exchange(order, data, sentence, reply)
                    if asked and asked[0].get("name") == "iniciar_cambio":
                        return asked
                    parts.append(str((asked[0].get("input") or {}).get("mensaje") or ""))
                elif "error" in done[2]:
                    parts.append(
                        f"I couldn't start the exchange for order {order}: {done[2]['error']}"
                    )
                else:
                    parts.append(self._exchanged(done[2].get("data") or {}))
                continue
            parts.append(self.order_status(order, data, offers))
        return reply(" ".join(p for p in parts if p))

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

    def recommend(
        self, call_input: dict[str, Any], data: dict[str, Any], message: str, corrected: bool
    ) -> str:
        """La mejor coincidencia de la búsqueda, con el precio y las variantes con stock que
        trajo la herramienta. En el modo "inventa_precio", la primera vez inventa el precio (como
        un modelo que lo saca de memoria) hasta que el control en código lo corrige."""
        products = data.get("products") or []
        if not products:
            return "Sorry, we don't carry that. Is there anything else I can help you find?"
        wanted = _words(message)
        product = max(products, key=lambda p: len(wanted & _words(str(p["name"]))))
        price = float(product["price"])
        if self.mode == "inventa_precio" and not corrected:
            price -= 50
        size = call_input.get("size")
        variants = product.get("variants") or []
        available = [v for v in variants if v.get("stock") is None or v["stock"] > 0]
        missing = [v for v in variants if v.get("stock") == 0]
        where = f" in size {size}" if size else ""
        if not available:
            return (
                f"The {product['name']} is out of stock{where} right now. Would you like me to "
                "look for something similar?"
            )
        colors = sorted({str(v["color"]) for v in available})
        text = (
            f"The {product['name']} is {money(price)}. It's in stock{where} in {', '.join(colors)}."
        )
        gone = sorted({str(v["color"]) for v in missing} - set(colors))
        if gone:
            text += f" {', '.join(gone)} is out of stock{where}."
        return f"{text} Would you like to buy it?"

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
    def order_status(order_id: Any, data: dict[str, Any], offers: bool = False) -> str:
        """`offers`: con un pedido atrasado, las opciones de la política para elegir."""
        text = EnglishAgent._status(order_id, data)
        if offers and data.get("delay"):
            text += (
                " To make up for the delay, I can give you a courtesy coupon for your next order "
                "or free express shipping on your next order. Which would you prefer?"
            )
        return text

    @staticmethod
    def _status(order_id: Any, data: dict[str, Any]) -> str:
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
