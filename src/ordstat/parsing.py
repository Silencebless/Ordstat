import csv
import ipaddress
import json
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from ordstat.models import (
    ConfigFormatError,
    CsvStructureError,
    FileAccessError,
    LogEntry,
)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

LOG_PATTERN = re.compile(
    r"^(\S+) \S+ \S+ \[(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})\] "
    r'"([A-Z]+) ([^" ]+) HTTP/\d\.\d" (\d{3}) (\d+|-) "[^"]*" "[^"]*" (\d+\.\d{3})$'
)

UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def load_rates(fp: Path) -> dict[str, Decimal]:
    try:
        with open(fp, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
        raise FileAccessError(f"Не удалось открыть файл курсов: {fp}") from err
    except json.JSONDecodeError as err:
        raise ConfigFormatError(f"Некорректный JSON в файле курсов: {fp}") from err

    if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
        raise ConfigFormatError(f"Неверная структура файла курсов: {fp}")

    rates: dict[str, Decimal] = {}
    for code, rate_val in data["rates"].items():
        if not isinstance(code, str) or not isinstance(rate_val, (str, int)):
            raise ConfigFormatError(f"Неверный формат курса для {code}")
        try:
            dec = Decimal(str(rate_val))
        except InvalidOperation as err:
            raise ConfigFormatError(f"Невалидное число курса для {code}") from err
        if dec <= 0:
            raise ConfigFormatError(f"Курс должен быть больше нуля: {code}")
        rates[code.upper()] = dec
    return rates


def load_city_aliases(fp: Path) -> dict[str, str]:
    try:
        with open(fp, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
        raise FileAccessError(f"Не удалось открыть файл городов: {fp}") from err
    except json.JSONDecodeError as err:
        raise ConfigFormatError(f"Некорректный JSON в файле городов: {fp}") from err

    if not isinstance(data, dict):
        raise ConfigFormatError(f"Ожидался JSON-объект в файле городов: {fp}")

    aliases: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ConfigFormatError("Ключи и значения в city_aliases должны быть строками")
        aliases[k.casefold()] = v
    return aliases


def read_orders(fp: Path) -> Iterator[tuple[str, ...]]:
    try:
        with open(fp, "r", encoding="utf-8-sig", newline="") as file:
            csv_reader = csv.reader(file)
            header = next(csv_reader, None)
            if header is None:
                return
            for row in csv_reader:
                padded = list(row[:8]) + [""] * max(0, 8 - len(row))
                yield tuple(padded)
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
        raise FileAccessError(f"Ошибка при открытии файла заказов: {fp}") from err
    except csv.Error as err:
        raise CsvStructureError(f"Ошибка чтения CSV файла: {fp}") from err


def read_logs(fp: Path) -> Iterator[str]:
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as file:
            for line in file:
                yield line.rstrip("\r\n")
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
        raise FileAccessError(f"Ошибка при открытии файла логов: {fp}") from err


def normalize_endpoint(method: str, raw_path: str) -> str:
    path_only = raw_path.split("?", 1)[0]
    segments = path_only.split("/")
    normalized: list[str] = []
    for seg in segments:
        if seg and seg.isascii() and seg.isdigit():
            normalized.append("{id}")
        elif UUID_PATTERN.match(seg):
            normalized.append("{uuid}")
        else:
            normalized.append(seg)
    return f"{method} {'/'.join(normalized)}"


def parse_log_line(line: str) -> LogEntry | None:
    if not line:
        return None
    match = LOG_PATTERN.match(line)
    if not match:
        return None

    (
        ip_str,
        day_s,
        mon_s,
        year_s,
        hour_s,
        min_s,
        sec_s,
        tz_s,
        method,
        raw_path,
        status_s,
        bytes_s,
        time_s,
    ) = match.groups()

    try:
        ipaddress.IPv4Address(ip_str)
    except ValueError:
        return None

    if method not in ALLOWED_METHODS:
        return None

    status = int(status_s)
    if not (100 <= status <= 599):
        return None

    month = MONTH_MAP.get(mon_s)
    if month is None:
        return None

    tz_sign = 1 if tz_s[0] == "+" else -1
    tz_hours = int(tz_s[1:3])
    tz_minutes = int(tz_s[3:5])
    if tz_hours > 23 or tz_minutes > 59:
        return None

    offset = timedelta(minutes=tz_sign * (tz_hours * 60 + tz_minutes))
    try:
        dt = datetime(
            int(year_s),
            month,
            int(day_s),
            int(hour_s),
            int(min_s),
            int(sec_s),
            tzinfo=timezone(offset),
        ).astimezone(MOSCOW_TZ)
    except ValueError:
        return None

    bytes_sent = 0 if bytes_s == "-" else int(bytes_s)
    proc_time = Decimal(time_s)
    endpoint = normalize_endpoint(method, raw_path)

    return LogEntry(
        ip=ip_str,
        timestamp=dt,
        method=method,
        endpoint=endpoint,
        status=status,
        bytes_sent=bytes_sent,
        processing_time=proc_time,
    )