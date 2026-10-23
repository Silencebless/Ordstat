import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from ordstat.aggregations import process_logs_stream, process_orders_stream
from ordstat.models import ConfigFormatError, OrdstatError
from ordstat.parsing import load_city_aliases, load_rates, read_logs, read_orders
from ordstat.report import RejectedCsvWriter, timed, write_json_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ordstat", description="Утилита аналитики заказов и логов")
    subparsers = parser.add_subparsers(dest="command", required=True)

    orders_p = subparsers.add_parser("orders", help="Построить отчёт по выгрузке заказов")
    orders_p.add_argument("--orders", type=Path, required=True, help="Путь к orders.csv")
    orders_p.add_argument("--rates", type=Path, required=True, help="Путь к rates.json")
    orders_p.add_argument("--aliases", type=Path, required=True, help="Путь к city_aliases.json")
    orders_p.add_argument("--out", type=Path, required=True, help="Путь к выходному JSON-отчёту")
    orders_p.add_argument("--rejected", type=Path, required=True, help="Путь к CSV с отклонёнными записями")
    orders_p.add_argument("--top-cities", type=int, default=10, help="Количество городов в топе")
    orders_p.add_argument("-v", "--verbose", action="store_true", help="Подробное логирование прогресса")

    logs_p = subparsers.add_parser("logs", help="Построить отчёт по логам веб-сервера")
    logs_p.add_argument("--log", type=Path, required=True, help="Путь к access.log")
    logs_p.add_argument("--out", type=Path, required=True, help="Путь к выходному JSON-отчёту")
    logs_p.add_argument("-v", "--verbose", action="store_true", help="Подробное логирование прогресса")

    return parser


@timed
def run_orders(
    orders_path: Path,
    rates_path: Path,
    aliases_path: Path,
    out_path: Path,
    rejected_path: Path,
    top_cities: int,
) -> None:
    if top_cities < 0:
        raise ConfigFormatError("--top-cities не может быть отрицательным")
    rates = load_rates(rates_path)
    aliases = load_city_aliases(aliases_path)
    with RejectedCsvWriter(rejected_path) as rej_writer:
        raw_stream = read_orders(orders_path)
        report = process_orders_stream(
            raw_rows=raw_stream,
            rates=rates,
            aliases=aliases,
            on_rejected=rej_writer.write,
            top_cities_limit=top_cities,
        )
    write_json_report(out_path, report)


@timed
def run_logs(log_path: Path, out_path: Path) -> None:
    lines_stream = read_logs(log_path)
    report = process_logs_stream(lines_stream)
    write_json_report(out_path, report)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        if args.command == "orders":
            run_orders(
                orders_path=args.orders,
                rates_path=args.rates,
                aliases_path=args.aliases,
                out_path=args.out,
                rejected_path=args.rejected,
                top_cities=args.top_cities,
            )
        elif args.command == "logs":
            run_logs(log_path=args.log, out_path=args.out)
    except OrdstatError as err:
        sys.stderr.write(f"Ошибка: {err}\n")
        return 3

    return 0