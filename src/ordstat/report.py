import csv
import functools
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, ParamSpec, TypeVar

from ordstat.models import FileAccessError, RejectedOrder

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

REJECTED_HEADER = [
    "row_number",
    "reason",
    "order_id",
    "user_id",
    "created_at",
    "status",
    "amount",
    "currency",
    "city",
    "items_count",
]


def timed(func: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - start
            logger.info("Этап '%s' выполнен за %.3f сек.", func.__name__, elapsed)

    return wrapper


class RejectedCsvWriter:
    def __init__(self, fp: Path) -> None:
        self._fp = fp
        self._file: Any = None
        self._writer: Any = None

    def __enter__(self) -> "RejectedCsvWriter":
        try:
            self._file = open(self._fp, "w", encoding="utf-8", newline="")
            self._writer = csv.writer(self._file)
            self._writer.writerow(REJECTED_HEADER)
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
            raise FileAccessError(f"Не удалось открыть файл для записи отклонённых строк: {self._fp}") from err
        return self

    def write(self, item: RejectedOrder) -> None:
        if self._writer is not None:
            self._writer.writerow([item.row_number, item.reason, *item.raw_fields])

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()


def write_json_report(fp: Path, report_data: Any) -> None:
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as err:
        raise FileAccessError(f"Не удалось записать отчёт в файл: {fp}") from err