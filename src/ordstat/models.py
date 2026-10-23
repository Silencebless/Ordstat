from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

#EXCEPTIONS
class OrdstatError(Exception):
    """Базовое исключение утилиты"""
class FileAccessError(OrdstatError):
    """Нет файла, нет прав доступа"""
class ConfigFormatError(OrdstatError):
    """Битый JSON или неверная схема данных"""
class CsvStructureError:
    """Отсутствуют заголовки, неверная кодировка или фатальная ошибка CSV"""

#ORDERS
@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    user_id: int
    created_at: datetime
    status: str
    amount: Decimal
    currency: str
    city: str
    items_count: int

@dataclass(frozen=True, slots=True)
class RejectedOrder:
    row_number: int
    reason: str
    raw_fields: tuple[str, ...]

class SummaryOrdersDict(TypedDict):
    rows_read: int
    rows_valid: int
    rows_rejected: int
    rejected_by_reason: dict[str, int]
    duplicates_replaced: int
    orders_unique: int

class DayStatDict(TypedDict):
    date: str
    revenue_rub: str
    orders: int

class CityStatDict(TypedDict):
    city: str
    orders: int
    revenue_rub: str
    aov_rub: str

class CancelMonthShareDict(TypedDict):
    month: str
    orders: int
    cancelled: int
    share_pct: str

class SuspiciousUser(TypedDict):
    user_id: int
    max_orders_in_window: int
    first_burst_start: str

class ReportOrdersDict(TypedDict):
    summary: SummaryOrdersDict
    revenue_rub: str
    top_days: list[DayStatDict]
    top_cities: list[CityStatDict]
    cancel_share_by_month: list[CancelMonthShareDict]
    suspicious_users: list[SuspiciousUser]

#LOGS

@dataclass(frozen=True, slots=True)
class LogEntry:
    ip: str
    timestamp: datetime
    method: str
    endpoint: str
    status: int
    bytes: int
    processing_time: Decimal

class SummaryLogsDict(TypedDict):
    lines_total: int
    lines_parsed: int
    lines_malformed: int
    status_classes: dict[str, int]
    error_rate_5xx_pct: str

class EndpointDict(TypedDict):
    endpoint: str
    count: int
    p50: str
    p95: str
    p99: str

class BruteforceIp(TypedDict):
    ip: str
    max_401_per_minute: int

class ReportLogsDict(TypedDict):
    summary: SummaryLogsDict
    top_endpoints: list[EndpointDict]
    bruteforce_ips: list[BruteforceIp]