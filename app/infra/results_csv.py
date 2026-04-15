from __future__ import annotations

import csv
import os
import threading
from datetime import datetime

from app.domain.models import TestResult

_WRITE_LOCK = threading.Lock()


CSV_COLUMNS: list[str] = [
    "Data da execução",
    "Fonte",
    "URL",
    "CEP",
    "Produto",
    "Valor do frete",
    "Moeda",
    "Tipo do frete",
    "Prazo de entrega",
    "Modo de entrega",
]


def _format_price_kind(value: str | None) -> str:
    kind = str(value or "").upper()
    if kind == "FREE":
        return "Grátis"
    if kind == "PAID":
        return "Pago"
    return "Indisponível"


def _flatten_result(result: TestResult) -> dict[str, str]:
    price = "" if result.freight.price is None else str(result.freight.price)

    return {
        "Data da execução": datetime.now().isoformat(timespec="seconds"),
        "Fonte": result.source,
        "URL": result.url,
        "CEP": result.cep,
        "Produto": result.product_name or "",
        "Valor do frete": price,
        "Moeda": result.freight.currency or "",
        "Tipo do frete": _format_price_kind(result.freight.price_kind),
        "Prazo de entrega": result.freight.delivery_time_text or "",
        "Modo de entrega": result.freight.delivery_mode or "",
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
