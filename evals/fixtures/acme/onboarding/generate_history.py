# ruff: noqa: E501  (plantillas de texto de las conversaciones)
"""Genera el historial sintético de Acme para el onboarding (Hito 4): 100 conversaciones.

Uso: python evals/fixtures/acme/onboarding/generate_history.py

Escribe en esta carpeta, con semilla fija (siempre el mismo resultado):
  whatsapp_export.zip  40 chats exportados de WhatsApp (un .txt por cliente, formato Android)
  tickets.csv          30 tickets de un helpdesk (una fila por mensaje)
  tickets.json         30 tickets de un helpdesk (un objeto por ticket)
  labels.json          tipo de caso real de cada conversación (solo para medir el descubrimiento)

Los datos personales (nombres, teléfonos, emails, DNI, CUIT y tarjetas) son inventados y están
sembrados a propósito: la anonimización los tiene que sacar todos antes de que lleguen a un modelo.
"""

import csv
import io
import json
import random
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261109
BUSINESS = "Acme Store"

FIRST = [
    "Lucía",
    "Martín",
    "Sofía",
    "Juan",
    "Valentina",
    "Mateo",
    "Camila",
    "Tomás",
    "Julieta",
    "Nicolás",
    "Agustina",
    "Facundo",
    "Micaela",
    "Santiago",
    "Florencia",
    "Lautaro",
    "Paula",
    "Gonzalo",
    "Rocío",
    "Bruno",
]
LAST = [
    "Gómez",
    "Fernández",
    "López",
    "Díaz",
    "Martínez",
    "Pérez",
    "Romero",
    "Sosa",
    "Álvarez",
    "Torres",
    "Ruiz",
    "Ramírez",
    "Flores",
    "Acosta",
    "Benítez",
    "Medina",
    "Herrera",
    "Suárez",
]
CARDS = ["4509 9535 6623 3704", "5031 7557 3453 0604", "3711 803032 57522", "4111 1111 1111 1111"]

# tipo -> (cantidad, aperturas del cliente, respuesta del agente, seguimiento, cierre, acción)
TYPES: dict[str, dict] = {
    "donde_esta_mi_pedido": {
        "n": 20,
        "open": [
            "hola, donde esta mi pedido {order}?",
            "buenas! cuando me llega el pedido {order}",
            "hace una semana compre y no me llego nada, pedido {order}",
            "me pasan el seguimiento del pedido {order}?",
        ],
        "agent": [
            "Hola {name}! El pedido {order} fue despachado y llega el {date}.",
            "Ya lo revisé: el {order} está en camino, la entrega estimada es el {date}.",
        ],
        "follow": ["me pasan el numero de seguimiento?", "llega a la direccion de siempre?", ""],
        "close": ["genial gracias", "ok gracias!!", "perfecto"],
        "system": False,
    },
    "cambiar_direccion": {
        "n": 15,
        "open": [
            "necesito cambiar la direccion de envio del pedido {order}",
            "me mude, se puede mandar el {order} a otra direccion?",
            "quiero que el pedido {order} vaya a {address}",
        ],
        "agent": [
            "Listo {name}, actualicé la dirección del pedido {order} a {address}.",
            "Hecho: el {order} ahora va a {address}. Todavía no se había despachado.",
        ],
        "follow": ["la nueva es {address}", ""],
        "close": ["mil gracias", "buenisimo"],
        "system": True,
    },
    "cancelar_pedido": {
        "n": 12,
        "open": [
            "quiero cancelar el pedido {order}",
            "me arrepenti, cancelen el {order} x favor",
            "como cancelo una compra? es el pedido {order}",
        ],
        "agent": [
            "Cancelé el pedido {order}. El reintegro se ve en tu tarjeta {card} en el próximo resumen.",
            "Listo, el {order} quedó cancelado y te devolvemos el dinero por el mismo medio.",
        ],
        "follow": ["y la plata cuando vuelve?", ""],
        "close": ["ok gracias", "dale"],
        "system": True,
    },
    "reembolso": {
        "n": 12,
        "open": [
            "quiero que me devuelvan la plata del pedido {order}, vino fallado",
            "me llego roto el {order}, quiero el reembolso",
            "pedi la devolucion del {order} hace dias y nada",
        ],
        "agent": [
            "Te inicié el reembolso del pedido {order}; una persona del equipo lo aprueba en 48 h.",
            "Ya cargué la devolución del {order}. El dinero vuelve en 5 a 10 días hábiles.",
        ],
        "follow": ["mi DNI es {dni} por si lo necesitan", "les mando foto?", ""],
        "close": ["gracias", "ok espero"],
        "system": True,
    },
    "cambio_por_talle": {
        "n": 12,
        "open": [
            "la remera me quedo chica, la puedo cambiar por un L?",
            "quiero cambiar las zapatillas por un numero mas",
            "se puede cambiar de talle un jean?",
        ],
        "agent": [
            "Sí, {name}: tenés 30 días para cambiarlo, sin uso y con etiqueta, en cualquier sucursal.",
            "Claro, el primer cambio es gratis. Podés ir a una sucursal o lo retiramos por tu casa.",
        ],
        "follow": ["y si no hay talle?", "tiene que tener la etiqueta?", ""],
        "close": ["joya", "gracias!"],
        "system": False,
    },
    "medios_de_pago_y_cuotas": {
        "n": 10,
        "open": [
            "tienen cuotas sin interes?",
            "puedo pagar con mercado pago?",
            "si pago con transferencia hay descuento?",
        ],
        "agent": [
            "Tenemos 3 y 6 cuotas sin interés con tarjetas bancarias, y 10% off pagando por transferencia.",
            "Aceptamos tarjetas, transferencia y billeteras virtuales. Con transferencia hay 10% de descuento.",
        ],
        "follow": ["con visa tambien?", "y con debito?", ""],
        "close": ["ok", "barbaro"],
        "system": False,
    },
    "facturacion": {
        "n": 10,
        "open": [
            "necesito factura A, mi CUIT es {cuit}",
            "no me llego la factura del pedido {order}",
            "me hicieron factura B y necesitaba A",
        ],
        "agent": [
            "La factura llega por email dentro de las 48 horas hábiles. Te la reenvío a {email}.",
            "Para factura A hay que cargar el CUIT al comprar; si ya se emitió, lo revisa administración.",
        ],
        "follow": ["mi mail es {email}", ""],
        "close": ["gracias", "ok"],
        "system": False,
    },
    "horarios_y_sucursales": {
        "n": 9,
        "open": ["a que hora abren?", "abren los domingos?", "donde queda la sucursal de rosario?"],
        "agent": [
            "Las sucursales abren de lunes a sábado de 10 a 20. Domingos y feriados, cerrado.",
            "En Rosario estamos en Córdoba 1500, de lunes a sábado de 10 a 20.",
        ],
        "follow": ["y los feriados?", ""],
        "close": ["gracias", "ok genial"],
        "system": False,
    },
}


def _person(rng: random.Random) -> dict[str, str]:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    plain = (first + "." + last).lower()
    for a, b in zip("áéíóú", "aeiou", strict=True):
        plain = plain.replace(a, b)
    return {
        "name": f"{first} {last}",
        "phone": f"+54 9 11 5{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
        "email": f"{plain}{rng.randint(1, 99)}@example.com",
        "dni": f"{rng.randint(20, 45)}.{rng.randint(100, 999)}.{rng.randint(100, 999)}",
        "cuit": f"20-{rng.randint(20_000_000, 45_000_000)}-{rng.randint(0, 9)}",
        "card": rng.choice(CARDS),
    }


def _conversation(rng: random.Random, kind: str, index: int, start: datetime) -> dict:
    spec = TYPES[kind]
    person = _person(rng)
    values = {
        **person,
        "order": str(rng.randint(1100, 1999)),
        "date": f"{rng.randint(10, 28)}/11",
        "address": rng.choice(
            [
                "Av. Corrientes 1234, CABA",
                "San Martín 555, Córdoba",
                "Belgrano 200, Mendoza",
                "Mitre 980, Rosario",
            ]
        ),
    }
    messages = [("customer", rng.choice(spec["open"]))]
    if rng.random() < 0.3:  # algunos clientes se presentan con nombre y teléfono
        messages.append(("customer", f"soy {person['name']}, mi cel es {person['phone']}"))
    messages.append(("agent", rng.choice(spec["agent"])))
    follow = rng.choice(spec["follow"])
    if follow:
        messages.append(("customer", follow))
        messages.append(("agent", "Sí, te confirmo. Cualquier cosa escribime por acá."))
    messages.append(("customer", rng.choice(spec["close"])))
    when = start + timedelta(days=index // 4, hours=rng.randint(9, 19), minutes=rng.randint(0, 59))
    turns = []
    for offset, (role, text) in enumerate(messages):
        turns.append(
            {
                "role": role,
                "author": person["name"] if role == "customer" else BUSINESS,
                "text": text.format(**values),
                "sent_at": (when + timedelta(minutes=2 * offset)).isoformat(timespec="minutes"),
            }
        )
    return {
        "id": f"acme-{index:03d}",
        "kind": kind,
        "customer": person,
        "messages": turns,
        "system_action": spec["system"],
    }


def build() -> list[dict]:
    rng = random.Random(SEED)
    kinds = [k for k, spec in TYPES.items() for _ in range(spec["n"])]
    rng.shuffle(kinds)
    start = datetime(2026, 8, 1)
    return [_conversation(rng, kind, i + 1, start) for i, kind in enumerate(kinds)]


def whatsapp_txt(conversation: dict) -> str:
    lines = []
    for m in conversation["messages"]:
        when = datetime.fromisoformat(m["sent_at"])
        lines.append(
            f"{when.day}/{when.month}/{when:%y}, {when:%H:%M} - {m['author']}: {m['text']}"
        )
    return "\n".join(lines) + "\n"


def write(conversations: list[dict]) -> None:
    whatsapp, csv_part, json_part = conversations[:40], conversations[40:70], conversations[70:]
    with zipfile.ZipFile(HERE / "whatsapp_export.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for c in whatsapp:
            name = f"Chat de WhatsApp con {c['customer']['name']} ({c['id']}).txt"
            bundle.writestr(name, whatsapp_txt(c))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ticket_id", "created_at", "author_type", "author_name", "body"])
    for c in csv_part:
        for m in c["messages"]:
            role = "end_user" if m["role"] == "customer" else "agent"
            writer.writerow([c["id"], m["sent_at"], role, m["author"], m["text"]])
    (HERE / "tickets.csv").write_text(buffer.getvalue(), encoding="utf-8", newline="")
    tickets = [
        {
            "id": c["id"],
            "requester": {"name": c["customer"]["name"], "email": c["customer"]["email"]},
            "comments": [
                {
                    "author_type": "end_user" if m["role"] == "customer" else "agent",
                    "body": m["text"],
                    "created_at": m["sent_at"],
                }
                for m in c["messages"]
            ],
        }
        for c in json_part
    ]
    (HERE / "tickets.json").write_text(
        json.dumps(tickets, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    labels = {
        c["id"]: {"tipo": c["kind"], "accion_en_sistema": c["system_action"]} for c in conversations
    }
    (HERE / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=1), "utf-8")
    seeded = sorted({v for c in conversations for k, v in c["customer"].items()})
    (HERE / "seeded_pii.json").write_text(json.dumps(seeded, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    data = build()
    write(data)
    print(f"{len(data)} conversaciones escritas en {HERE}")
