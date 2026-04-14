from __future__ import annotations

import csv
import os
import threading
from datetime import datetime

from app.domain.models import TestResult

_WRITE_LOCK = threading.Lock()


CSV_COLUMNS: list[str] = [
    "run_at",
    "source",
    "url",
    "cep",
    "status",
    "product_name",
    "freight_price",
    "freight_currency",
    "freight_delivery_time_text",
    "freight_delivery_mode",
    "errors",
    "artifact_screenshot",
    "artifact_html",
]


def _flatten_result(result: TestResult) -> dict[str, str]:
    errors = " | ".join(result.errors) if result.errors else ""
    price = "" if result.freight.price is None else str(result.freight.price)

    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "source": result.source,
        "url": result.url,
        "cep": result.cep,
        "status": result.status,
        "product_name": result.product_name or "",
        "freight_price": price,
        "freight_currency": result.freight.currency or "",
        "freight_delivery_time_text": result.freight.delivery_time_text or "",
        "freight_delivery_mode": result.freight.delivery_mode or "",
        "errors": errors,
        "artifact_screenshot": result.artifacts.screenshot or "",
        "artifact_html": result.artifacts.html or "",
    }


def ensure_results_csv(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return

    with _WRITE_LOCK:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=";")
            writer.writeheader()


def append_result(path: str, result: TestResult) -> None:
    ensure_results_csv(path)
    row = _flatten_result(result)

    with _WRITE_LOCK:
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=";")
            writer.writerow(row)

