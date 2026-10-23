import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from ordstat.models import Order, RejectedOrder

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

ORDER_ID_PATTERN = re.compile(r"^ORD-[0-9]{6}$")
ASCII_DIGITS_PATTERN = re.compile(r"^[0-9]+$")
ISO_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
DMY_DATE_PATTERN = re.compile(r"^[0-9]{2}\.[0-9]{2}\.[0-9]{4} [0-9]{2}:[0-9]{2}$")
AMOUNT_PATTERN = re.compile(r"^[0-9]+(\.[0-9]+)?$")

STATUS_MAPPING = {
    "paid": "paid",
    "оплачен": "paid",
    "created": "created",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "отменён": "cancelled",
    "отменен": "cancelled",
    "refunded": "refunded",
    "shipped": "shipped",
    "delivered": "delivered",
}


def parse_order_id(val: str) -> str | None:
    cleaned = val.strip()
    if not ORDER_ID_PATTERN.match(cleaned):
        return None
    return cleaned


def parse_user_id(val: str) -> int | None:
    cleaned = val.strip()
    if not ASCII_DIGITS_PATTERN.match(cleaned):
        return None
    uid = int(cleaned)
    return uid if uid >= 1 else None


def parse_created_at(val: str) -> datetime | None:
    cleaned = val.strip()
    if ISO_DATE_PATTERN.match(cleaned):
        try:
            dt = datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=MOSCOW_TZ)
        except ValueError:
            return None

    if DMY_DATE_PATTERN.match(cleaned):
        try:
            dt = datetime.strptime(cleaned, "%d.%m.%Y %H:%M")
            return dt.replace(tzinfo=MOSCOW_TZ)
        except ValueError:
            return None

    if len(cleaned) == 10 and ASCII_DIGITS_PATTERN.match(cleaned):
        try:
            dt_utc = datetime.fromtimestamp(int(cleaned), tz=timezone.utc)
            return dt_utc.astimezone(MOSCOW_TZ)
        except (ValueError, OverflowError, OSError):
            return None

    return None


def parse_status(val: str) -> str | None:
    cleaned = val.strip().casefold()
    return STATUS_MAPPING.get(cleaned)


def parse_amount(val: str) -> Decimal | None:
    cleaned = val.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not AMOUNT_PATTERN.match(cleaned):
        return None
    try:
        dec = Decimal(cleaned)
    except InvalidOperation:
        return None
    return dec if dec > 0 else None


def parse_currency(val: str, rates: dict[str, Decimal]) -> str | None:
    cleaned = val.strip().upper()
    return cleaned if cleaned in rates else None


def parse_city(val: str, aliases: dict[str, str]) -> str:
    collapsed = " ".join(val.split())
    if not collapsed:
        return "Не указан"
    key = collapsed.casefold()
    return aliases.get(key, collapsed)


def parse_items_count(val: str) -> int | None:
    cleaned = val.strip()
    if not ASCII_DIGITS_PATTERN.match(cleaned):
        return None
    count = int(cleaned)
    return count if count >= 1 else None


def validate_order_row(
    row_number: int,
    raw_fields: tuple[str, ...],
    rates: dict[str, Decimal],
    aliases: dict[str, str],
) -> Order | RejectedOrder:
    f_order_id, f_user_id, f_created_at, f_status, f_amount, f_currency, f_city, f_items = raw_fields

    order_id = parse_order_id(f_order_id)
    if order_id is None:
        return RejectedOrder(row_number, "invalid_order_id", raw_fields)

    user_id = parse_user_id(f_user_id)
    if user_id is None:
        return RejectedOrder(row_number, "invalid_user_id", raw_fields)

    created_at = parse_created_at(f_created_at)
    if created_at is None:
        return RejectedOrder(row_number, "invalid_date", raw_fields)

    status = parse_status(f_status)
    if status is None:
        return RejectedOrder(row_number, "unknown_status", raw_fields)

    amount = parse_amount(f_amount)
    if amount is None:
        return RejectedOrder(row_number, "invalid_amount", raw_fields)

    currency = parse_currency(f_currency, rates)
    if currency is None:
        return RejectedOrder(row_number, "unknown_currency", raw_fields)

    items_count = parse_items_count(f_items)
    if items_count is None:
        return RejectedOrder(row_number, "invalid_items_count", raw_fields)

    city = parse_city(f_city, aliases)
    amount_rub = (amount * rates[currency]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return Order(
        order_id=order_id,
        user_id=user_id,
        created_at=created_at,
        status=status,
        amount=amount,
        currency=currency,
        amount_rub=amount_rub,
        city=city,
        items_count=items_count,
    )