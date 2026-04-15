from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class FreightResult:
    price: Optional[float] = None
    currency: str = "BRL"
    # Canonical contract:
    # - price == 0.0 => free shipping
    # - price is None => unknown/unavailable
    # price_kind makes the semantics explicit for clients.
    price_kind: str = "UNKNOWN"  # FREE | PAID | UNKNOWN
    price_text: Optional[str] = None
    delivery_time_text: Optional[str] = None
    delivery_mode: Optional[str] = None
    options: list[dict] = field(default_factory=list)


@dataclass
class ArtifactResult:
    screenshot: Optional[str] = None
    html: Optional[str] = None


@dataclass
class TestResult:
    source: str
    url: str
    cep: str
    status: str
    product_name: Optional[str] = None
    freight: FreightResult = field(default_factory=FreightResult)
    errors: list[str] = field(default_factory=list)
    artifacts: ArtifactResult = field(default_factory=ArtifactResult)

    def to_dict(self) -> dict:
        return asdict(self)
