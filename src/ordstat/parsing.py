import csv
from collections.abc import Iterator
from pathlib import Path

from ordstat.models import FileAccessError, ConfigFormatError


def read_orders(fp: Path) -> Iterator[dict] | None:
    try:
        with open(fp, "r", encoding="utf-8-sig", newline="") as file:
            csv_reader = csv.DictReader(file)
            yield from csv_reader
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
        raise FileAccessError(f"Критическая ошибка при открытии файла {fp}") from err


def read_logs(fp: Path) -> Iterator[str] | None:
    try:
        with open(fp, "r", encoding="utf-8-sig") as file:
            yield from file
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
        raise FileAccessError(f"Критическая ошибка при открытии файла {fp}") from err


def reader(fp: Path) -> Iterator[dict] | Iterator[str] | None:
    suffix = fp.suffix.lower()

    if suffix == ".csv":
        return read_orders(fp)
    elif suffix == ".log":
        return read_logs(fp)
    else:
        raise ConfigFormatError(f"Неверный формат файла {fp}")
