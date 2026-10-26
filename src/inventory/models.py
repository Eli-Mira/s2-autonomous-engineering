from dataclasses import dataclass
from typing import Optional


@dataclass
class Product:
    id: int
    sku: str
    name: str
    quantity: int
    price_cents: int
    created_at: str
    updated_at: str

    @property
    def price_dollars(self) -> float:
        return self.price_cents / 100.0


@dataclass
class StockMovement:
    id: int
    product_id: int
    delta: int
    reason: Optional[str]
    created_at: str
