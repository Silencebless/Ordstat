import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ordstat.aggregations import (
    find_suspicious_users,
    process_logs_stream,
    process_orders_stream,
)
from ordstat.cli import main
from ordstat.models import RejectedOrder
from ordstat.parsing import parse_log_line
from ordstat.validation import (
    parse_amount,
    parse_city,
    parse_created_at,
    parse_status,
    validate_order_row,
)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


@pytest.mark.parametrize(
    ("raw_date", "expected"),
    [
        ("2026-03-05T10:15:30", datetime(2026, 3, 5, 10, 15, 30, tzinfo=MOSCOW_TZ)),
        ("05.03.2026 10:15", datetime(2026, 3, 5, 10, 15, 0, tzinfo=MOSCOW_TZ)),
        ("1772694930", datetime.fromtimestamp(1772694930, tz=MOSCOW_TZ)),
        ("2026-3-5T10:15:30", None),
        ("2026-02-30T10:00:00", None),
        ("12345", None),
    ],
)
def test_parse_created_at(raw_date: str, expected: datetime | None) -> None:
    assert parse_created_at(raw_date) == expected


@pytest.mark.parametrize(
    ("raw_amount", "expected"),
    [
        ("100.50", Decimal("100.50")),
        (" 1 000,25 ", Decimal("1000.25")),
        ("1\u00a0500", Decimal(1500)),
        ("0", None),
        ("-10", None),
        ("1e5", None),
        ("NaN", None),
        ("1.000,00", None),
    ],
)
def test_parse_amount(raw_amount: str, expected: Decimal | None) -> None:
    assert parse_amount(raw_amount) == expected


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("PAID", "paid"),
        (" Оплачен ", "paid"),
        ("отменён", "cancelled"),
        ("canceled", "cancelled"),
        ("unknown", None),
    ],
)
def test_parse_status(raw_status: str, expected: str | None) -> None:
    assert parse_status(raw_status) == expected


@pytest.mark.parametrize(
    ("raw_city", "expected"),
    [
        ("", "Не указан"),
        ("   ", "Не указан"),
        ("  москва ", "Москва"),
        ("Нижний   Новгород", "Нижний Новгород"),
    ],
)
def test_parse_city(raw_city: str, expected: str) -> None:
    aliases = {"москва": "Москва"}
    assert parse_city(raw_city, aliases) == expected


@pytest.mark.parametrize(
    ("row", "expected_reason"),
    [
        (("ord-123456", "1", "2026-03-01T10:00:00", "paid", "100", "RUB", "Москва", "1"), "invalid_order_id"),
        (("ORD-123456", "0", "2026-03-01T10:00:00", "paid", "100", "RUB", "Москва", "1"), "invalid_user_id"),
        (("ORD-123456", "1", "bad-date", "paid", "100", "RUB", "Москва", "1"), "invalid_date"),
        (("ORD-123456", "1", "2026-03-01T10:00:00", "bad", "100", "RUB", "Москва", "1"), "unknown_status"),
        (("ORD-123456", "1", "2026-03-01T10:00:00", "paid", "-5", "RUB", "Москва", "1"), "invalid_amount"),
        (("ORD-123456", "1", "2026-03-01T10:00:00", "paid", "100", "EUR", "Москва", "1"), "unknown_currency"),
        (("ORD-123456", "1", "2026-03-01T10:00:00", "paid", "100", "RUB", "Москва", "0"), "invalid_items_count"),
    ],
)
def test_validation_rejection_reasons(row: tuple[str, ...], expected_reason: str) -> None:
    res = validate_order_row(1, row, {"RUB": Decimal(1)}, {})
    assert isinstance(res, RejectedOrder)
    assert res.reason == expected_reason


def test_suspicious_window_boundary_600_vs_601() -> None:
    base = 1772694000
    t_600 = [datetime.fromtimestamp(base + offset, tz=MOSCOW_TZ) for offset in (0, 100, 200, 300, 600)]
    t_601 = [datetime.fromtimestamp(base + offset, tz=MOSCOW_TZ) for offset in (0, 100, 200, 300, 601)]

    res_600 = find_suspicious_users({1: t_600})
    assert len(res_600) == 1
    assert res_600[0]["max_orders_in_window"] == 5

    res_601 = find_suspicious_users({1: t_601})
    assert len(res_601) == 0


def test_month_boundary_moscow_time() -> None:
    ts = str(int(datetime(2026, 3, 31, 21, 30, 0, tzinfo=ZoneInfo("UTC")).timestamp()))
    rows = [("ORD-000001", "1", ts, "cancelled", "100", "RUB", "Москва", "1")]
    report = process_orders_stream(rows, {"RUB": Decimal(1)}, {}, lambda _: None)
    assert report["cancel_share_by_month"][0]["month"] == "2026-04"


def test_logs_parsing_and_aggregation() -> None:
    line = (
        '203.0.113.7 - - [05/Jun/2026:10:15:32 +0300] '
        '"GET /api/v1/orders/12345/550e8400-e29b-41d4-a716-446655440000?a=1 HTTP/1.1" '
        '401 512 "-" "curl" 0.120'
    )
    entry = parse_log_line(line)
    assert entry is not None
    assert entry.endpoint == "GET /api/v1/orders/{id}/{uuid}"

    lines = [line] * 20 + ["malformed line"]
    report = process_logs_stream(lines)
    assert report["summary"]["lines_total"] == 21
    assert report["summary"]["lines_parsed"] == 20
    assert report["summary"]["lines_malformed"] == 1
    assert report["bruteforce_ips"] == [{"ip": "203.0.113.7", "max_401_per_minute": 20}]


def test_cli_orders_and_logs_end_to_end(tmp_path: Path) -> None:
    assert main([
        "orders",
        "--orders", str(tmp_path / "missing.csv"),
        "--rates", "r",
        "--aliases", "a",
        "--out", "o",
        "--rejected", "rej",
    ]) == 3

    rates_f = tmp_path / "rates.json"
    aliases_f = tmp_path / "aliases.json"
    orders_f = tmp_path / "orders.csv"
    out_orders_f = tmp_path / "orders_out.json"
    rej_f = tmp_path / "rejected.csv"

    rates_f.write_text('{"base": "RUB", "rates": {"RUB": "1", "USD": "90.5"}}', encoding="utf-8")
    aliases_f.write_text('{"спб": "Санкт-Петербург"}', encoding="utf-8")
    orders_f.write_text(
        "order_id,user_id,created_at,status,amount,currency,city,items_count\n"
        "ORD-000001,1,2026-03-01T10:00:00,paid,100,RUB,спб,1\n"
        "ORD-000001,1,2026-03-01T11:00:00,paid,200,RUB,спб,2\n"
        "bad_id,1,2026-03-01T11:00:00,paid,200,RUB,спб,2\n",
        encoding="utf-8",
    )
    assert main([
        "orders",
        "--orders", str(orders_f),
        "--rates", str(rates_f),
        "--aliases", str(aliases_f),
        "--out", str(out_orders_f),
        "--rejected", str(rej_f),
        "-v",
    ]) == 0
    orders_data = json.loads(out_orders_f.read_text(encoding="utf-8"))
    assert orders_data["summary"]["orders_unique"] == 1
    assert orders_data["summary"]["duplicates_replaced"] == 1
    assert orders_data["revenue_rub"] == "200.00"

    log_file = tmp_path / "access.log"
    out_file = tmp_path / "out.json"
    log_file.write_text(
        '203.0.113.7 - - [05/Jun/2026:10:15:32 +0300] "GET /ping HTTP/1.1" 200 10 "-" "curl" 0.010\n',
        encoding="utf-8",
    )
    assert main(["logs", "--log", str(log_file), "--out", str(out_file), "-v"]) == 0
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary"]["lines_parsed"] == 1