from __future__ import annotations

import re


def normalize_cep(value: str | None) -> str:
    raw = (value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 8:
        return f"{digits[:5]}-{digits[5:]}"
    return raw
