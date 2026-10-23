import re
import json
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from json import JSONDecodeError

from ordstat import RejectedOrder, Order

PAYMENT_STATUSES = ("paid", "created", "cancelled", "refunded", "shipped", "delivered")

ORDER_ID_PATTERN = re.compile(r"^ORD-[0-9]{6}$")
USER_ID_PATTERN = re.compile(r"^[0-9]+$")
ITEMS_COUNT_PATTERN = re.compile(r"^[0-9]+$")

RATES = {}
CITY_ALIASES = {}
try:
    with open("rates.json", "r", encoding="utf-8") as f:
        RATES = json.load(f)
    with open("city_aliases.json", "r", encoding="utf-8-sig") as f:
        CITY_ALIASES = json.load(f)
except (JSONDecodeError, FileNotFoundError):
    pass


def parse_order_id(row: dict[str, str]) -> str | None:
    order_id = row.get("order_id", "-").strip()
    if not ORDER_ID_PATTERN.match(order_id):
        return None
    return order_id


def parse_user_id(row: dict[str, str]) -> int | None:
    user_id_str = row.get("user_id", "-").strip()
    if not USER_ID_PATTERN.match(user_id_str):
        return None

    user_id = int(user_id_str)
    if user_id == 0:
        return None
    return user_id


def parse_created_at(row: dict[str, str]) -> datetime | None:
    created_at = row.get("created_at", "-").strip()

    try:
        return datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass

    try:
        return datetime.strptime(created_at, "%d.%m.%Y %H:%M")
    except ValueError:
        pass

    if created_at.isdigit() and len(created_at) == 10:
        try:
            return datetime.fromtimestamp(int(created_at))
        except (ValueError, OverflowError, OSError):
            pass

    return None


def parse_status(row: dict[str, str]) -> str | None:
    status_raw = row.get("status", "-").strip().casefold()

    status_mapping = {
        'paid': 'paid',
        'оплачен': 'paid',
        'created': 'created',
        'cancelled': 'cancelled',
        'canceled': 'cancelled',
        'отменён': 'cancelled',
        'отменен': 'cancelled',
        'refunded': 'refunded',
        'shipped': 'shipped',
        'delivered': 'delivered'
    }

    final_status = status_mapping.get(status_raw)
    if final_status in PAYMENT_STATUSES:
        return final_status
    return None


def parse_amount(row: dict[str, str]) -> Decimal | None:
    if "amount" not in row:
        return None

    clean_str = str(row["amount"]).replace(' ', '').replace('\xa0', '')
    clean_str = clean_str.replace(',', '.')

    if not re.match(r'^\d+(\.\d+)?$', clean_str):
        return None

    try:
        amount = Decimal(clean_str)
    except (InvalidOperation, TypeError):
        return None

    if amount <= 0:
        return None

    return amount


def parse_currency(row: dict[str, str]) -> str | None:
    currency = row.get("currency", "-").strip().upper()
    if currency not in RATES:
        return None
    return currency


def parse_city(row: dict[str, str]) -> str:
    raw_city = row.get("city")
    if raw_city is None:
        raw_city = "unknown"
    city = str(raw_city).strip().lower()
    if city in CITY_ALIASES:
        return str(CITY_ALIASES[city])
    return city


def parse_items_count(row: dict[str, str]) -> int | None:
    items_count_str = row.get("items_count", "-").strip()
    if not ITEMS_COUNT_PATTERN.match(items_count_str):
        return None

    items_count = int(items_count_str)
    if items_count < 1:
        return None
    return items_count


def valid_orders(raw_rows: Iterator[dict]) -> tuple[list[Order], list[RejectedOrder]]:
    rejected = []
    valid = []

    fields_to_collect = [
        "order_id", "user_id", "created_at", "status",
        "amount", "currency", "city", "items_count"
    ]

    for line, row in enumerate(raw_rows, start=1):
        row_tuple = tuple(str(row.get(field)) if row.get(field) is not None else "-" for field in fields_to_collect)

        order_id = parse_order_id(row)
        if order_id is None:
            rejected.append(RejectedOrder(int(line), "invalid_order_id", row_tuple))
            continue

        user_id = parse_user_id(row)
        if user_id is None:
            rejected.append(RejectedOrder(int(line), "invalid_user_id", row_tuple))
            continue

        created_at = parse_created_at(row)
        if created_at is None:
            rejected.append(RejectedOrder(int(line), "invalid_date", row_tuple))
            continue

        status = parse_status(row)
        if status is None:
            rejected.append(RejectedOrder(int(line), "unknown_status", row_tuple))
            continue

        amount = parse_amount(row)
        if amount is None:
            rejected.append(RejectedOrder(int(line), "invalid_amount", row_tuple))
            continue

        currency = parse_currency(row)
        if currency is None:
            rejected.append(RejectedOrder(int(line), "unknown_currency", row_tuple))
            continue

        city = parse_city(row)

        items_count = parse_items_count(row)
        if items_count is None:
            rejected.append(RejectedOrder(int(line), "invalid_items_count", row_tuple))
            continue

        valid.append(Order(
            order_id,
            user_id,
            created_at,
            status,
            amount,
            currency,
            city,
            items_count
        ))

    return valid, rejected
