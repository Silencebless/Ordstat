import logging
import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from ordstat.models import (
    BruteforceIp,
    CancelMonthShareDict,
    CityStatDict,
    DayStatDict,
    EndpointDict,
     Order,
    RejectedOrder,
    ReportLogsDict,
    ReportOrdersDict,
    SummaryLogsDict,
    SummaryOrdersDict,
    SuspiciousUser,
)
from ordstat.parsing import parse_log_line
from ordstat.validation import validate_order_row

logger = logging.getLogger(__name__)

REVENUE_STATUSES = frozenset({"paid", "shipped", "delivered"})


def fmt_dec(val: Decimal) -> str:
    return f"{val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def fmt_time(val: Decimal) -> str:
    return f"{val.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP):.3f}"


def find_suspicious_users(user_timestamps: dict[int, list[datetime]]) -> list[SuspiciousUser]:
    result: list[SuspiciousUser] = []
    for user_id in sorted(user_timestamps.keys()):
        times = sorted(user_timestamps[user_id])
        n = len(times)
        if n < 5:
            continue

        max_in_window = 0
        first_burst: datetime | None = None
        right = 0

        for left in range(n):
            while right < n and (times[right] - times[left]).total_seconds() <= 600:
                right += 1
            count = right - left
            if count > max_in_window:
                max_in_window = count
            if count >= 5 and first_burst is None:
                first_burst = times[left]

        if max_in_window >= 5 and first_burst is not None:
            result.append(
                SuspiciousUser(
                    user_id=user_id,
                    max_orders_in_window=max_in_window,
                    first_burst_start=first_burst.isoformat(),
                )
            )
    return result


def process_orders_stream(
    raw_rows: Iterable[tuple[str, ...]],
    rates: dict[str, Decimal],
    aliases: dict[str, str],
    on_rejected: Callable[[RejectedOrder], None],
    top_cities_limit: int = 10,
) -> ReportOrdersDict:
    rows_read = 0
    rows_valid = 0
    rows_rejected = 0
    rejected_counts: dict[str, int] = defaultdict(int)
    duplicates_replaced = 0
    unique_orders: dict[str, Order] = {}

    for row_num, raw_row in enumerate(raw_rows, start=1):
        rows_read += 1
        if rows_read % 50_000 == 0:
            logger.info("Обработано %d строк заказов...", rows_read)

        res = validate_order_row(row_num, raw_row, rates, aliases)
        if isinstance(res, RejectedOrder):
            rows_rejected += 1
            rejected_counts[res.reason] += 1
            on_rejected(res)
        else:
            rows_valid += 1
            if res.order_id in unique_orders:
                duplicates_replaced += 1
            unique_orders[res.order_id] = res

    revenue_rub = Decimal("0.00")
    day_rev: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    day_orders: dict[str, int] = defaultdict(int)

    city_rev: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    city_orders: dict[str, int] = defaultdict(int)

    month_total: dict[str, int] = defaultdict(int)
    month_cancelled: dict[str, int] = defaultdict(int)

    user_times: dict[int, list[datetime]] = defaultdict(list)

    for order in unique_orders.values():
        month_key = order.created_at.strftime("%Y-%m")
        month_total[month_key] += 1
        if order.status == "cancelled":
            month_cancelled[month_key] += 1

        user_times[order.user_id].append(order.created_at)

        if order.status in REVENUE_STATUSES:
            revenue_rub += order.amount_rub
            day_key = order.created_at.strftime("%Y-%m-%d")
            day_rev[day_key] += order.amount_rub
            day_orders[day_key] += 1

            city_rev[order.city] += order.amount_rub
            city_orders[order.city] += 1

    sorted_days = sorted(day_rev.keys(), key=lambda d: (-day_rev[d], d))[:5]
    top_days: list[DayStatDict] = [
        DayStatDict(date=d, revenue_rub=fmt_dec(day_rev[d]), orders=day_orders[d])
        for d in sorted_days
    ]

    sorted_cities = sorted(city_rev.keys(), key=lambda c: (-city_rev[c], c))[:top_cities_limit]
    top_cities: list[CityStatDict] = []
    for c in sorted_cities:
        c_rev = city_rev[c]
        c_cnt = city_orders[c]
        aov = (c_rev / Decimal(c_cnt)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        top_cities.append(
            CityStatDict(city=c, orders=c_cnt, revenue_rub=fmt_dec(c_rev), aov_rub=fmt_dec(aov))
        )

    cancel_share: list[CancelMonthShareDict] = []
    for m in sorted(month_total.keys()):
        tot = month_total[m]
        canc = month_cancelled[m]
        pct = (Decimal(canc) * Decimal(100) / Decimal(tot)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        cancel_share.append(
            CancelMonthShareDict(month=m, orders=tot, cancelled=canc, share_pct=fmt_dec(pct))
        )

    suspicious = find_suspicious_users(user_times)

    summary = SummaryOrdersDict(
        rows_read=rows_read,
        rows_valid=rows_valid,
        rows_rejected=rows_rejected,
        rejected_by_reason={k: rejected_counts[k] for k in sorted(rejected_counts) if rejected_counts[k] > 0},
        duplicates_replaced=duplicates_replaced,
        orders_unique=len(unique_orders),
    )

    return ReportOrdersDict(
        summary=summary,
        revenue_rub=fmt_dec(revenue_rub),
        top_days=top_days,
        top_cities=top_cities,
        cancel_share_by_month=cancel_share,
        suspicious_users=suspicious,
    )


def nearest_rank_percentile(sorted_vals: list[Decimal], p: int) -> Decimal:
    n = len(sorted_vals)
    k = math.ceil(p * n / 100)
    return sorted_vals[k - 1]


def process_logs_stream(lines: Iterable[str]) -> ReportLogsDict:
    lines_total = 0
    lines_parsed = 0
    lines_malformed = 0
    status_classes: dict[str, int] = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}

    endpoint_times: dict[str, list[Decimal]] = defaultdict(list)
    ip_minute_401: dict[tuple[str, str], int] = defaultdict(int)

    for line in lines:
        lines_total += 1
        if lines_total % 50_000 == 0:
            logger.info("Обработано %d строк логов...", lines_total)

        entry = parse_log_line(line)
        if entry is None:
            lines_malformed += 1
            continue

        lines_parsed += 1
        cls_key = f"{entry.status // 100}xx"
        if cls_key in status_classes:
            status_classes[cls_key] += 1

        endpoint_times[entry.endpoint].append(entry.processing_time)

        if entry.status == 401:
            minute_key = entry.timestamp.strftime("%Y-%m-%d %H:%M")
            ip_minute_401[(entry.ip, minute_key)] += 1

    if lines_parsed > 0:
        err_rate = (Decimal(status_classes["5xx"]) * Decimal(100) / Decimal(lines_parsed)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        err_rate = Decimal("0.00")

    sorted_endpoints = sorted(
        endpoint_times.keys(), key=lambda ep: (-len(endpoint_times[ep]), ep)
    )[:10]

    top_endpoints: list[EndpointDict] = []
    for ep in sorted_endpoints:
        times = sorted(endpoint_times[ep])
        top_endpoints.append(
            EndpointDict(
                endpoint=ep,
                count=len(times),
                p50=fmt_time(nearest_rank_percentile(times, 50)),
                p95=fmt_time(nearest_rank_percentile(times, 95)),
                p99=fmt_time(nearest_rank_percentile(times, 99)),
            )
        )

    ip_max_401: dict[str, int] = defaultdict(int)
    for (ip, _), cnt in ip_minute_401.items():
        if cnt > ip_max_401[ip]:
            ip_max_401[ip] = cnt

    bruteforce_ips: list[BruteforceIp] = [
        BruteforceIp(ip=ip, max_401_per_minute=cnt)
        for ip, cnt in sorted(ip_max_401.items(), key=lambda item: (-item[1], item[0]))
        if cnt >= 20
    ]

    summary = SummaryLogsDict(
        lines_total=lines_total,
        lines_parsed=lines_parsed,
        lines_malformed=lines_malformed,
        status_classes=status_classes,
        error_rate_5xx_pct=fmt_dec(err_rate),
    )

    return ReportLogsDict(
        summary=summary,
        top_endpoints=top_endpoints,
        bruteforce_ips=bruteforce_ips,
    )