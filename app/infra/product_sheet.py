from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from openpyxl import load_workbook


@dataclass(frozen=True)
class ProductInputRow:
    product_name: str
    product_id: str
    url: str


EXPECTED_HEADERS = ["nome do produto", "id do produto", "link do produto"]


def parse_products_csv(data: bytes) -> list[ProductInputRow]:
    text = data.decode("utf-8-sig", errors="replace")
    f = io.StringIO(text)
    reader = csv.reader(f, delimiter=";")

    rows = list(reader)
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0][:3]]
    if header != EXPECTED_HEADERS:
        raise ValueError("Cabeçalhos inválidos no CSV (esperado: Nome do produto;ID do produto;Link do produto).")

    out: list[ProductInputRow] = []
    for r in rows[1:]:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        name = (r[0] if len(r) > 0 else "").strip()
        pid = (r[1] if len(r) > 1 else "").strip()
        url = (r[2] if len(r) > 2 else "").strip()
        if not name and not pid and not url:
            continue
        if not url:
            continue
        out.append(ProductInputRow(product_name=name, product_id=pid, url=url))
    return out


def parse_products_xlsx(data: bytes) -> list[ProductInputRow]:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active

    # Try to locate header row by scanning first 20 rows and 3 columns
    header_row_idx: int | None = None
    for i in range(1, 21):
        values = [
            (ws.cell(row=i, column=1).value or ""),
            (ws.cell(row=i, column=2).value or ""),
            (ws.cell(row=i, column=3).value or ""),
        ]
        normalized = [str(v).strip().lower() for v in values]
        if normalized == EXPECTED_HEADERS:
            header_row_idx = i
            break

    if header_row_idx is None:
        raise ValueError("Cabeçalhos não encontrados no XLSX (Nome do produto / ID do produto / Link do produto).")

    out: list[ProductInputRow] = []
    for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
        if not row or all((c is None or str(c).strip() == "") for c in row[:3]):
            continue
        name = (str(row[0]).strip() if len(row) > 0 and row[0] is not None else "")
        pid = (str(row[1]).strip() if len(row) > 1 and row[1] is not None else "")
        url = (str(row[2]).strip() if len(row) > 2 and row[2] is not None else "")
        if not url:
            continue
        out.append(ProductInputRow(product_name=name, product_id=pid, url=url))

    return out


def parse_products_file(filename: str, data: bytes) -> list[ProductInputRow]:
    name = (filename or "").lower().strip()
    if name.endswith(".csv"):
        return parse_products_csv(data)
    if name.endswith(".xlsx"):
        return parse_products_xlsx(data)
    raise ValueError("Formato de arquivo não suportado. Envie .xlsx ou .csv.")

