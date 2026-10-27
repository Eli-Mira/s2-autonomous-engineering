# TASK_D API CONTRACT — READ-ONLY, DO NOT MODIFY
# This file is provided to the coding worker as the authoritative interface specification.
# The acceptance tests (tests/test_inventory.py) are IMMUTABLE and outside the model's write permissions.

## Platform-Specific Requirements (CRITICAL — read before implementing)

### Timestamps (ISO 8601 Strings)
- `created_at` and `updated_at` MUST be ISO 8601 strings (e.g., "2026-10-26T01:00:00.000000").
- Tests call `datetime.fromisoformat(product.created_at)` — this requires a `str`, NOT a `datetime` object.
- SQLite returns timestamps as strings by default via `CURRENT_TIMESTAMP`. Do NOT wrap them in `datetime.fromisoformat()` or `datetime.now()`.
- For `record_movement`, use the database's `CURRENT_TIMESTAMP` value and return it as a string.
- In models.py, type these fields as `str`, not `datetime`.

### SQLite Connection Lifecycle (Windows — CRITICAL)
- Use connection-per-operation: open a connection, execute, commit, close — in EVERY method.
- Do NOT store a persistent connection in `__init__` or as an instance variable (`self.conn`, `self._conn`).
- Windows file locking prevents test teardown from deleting temp DB files if connections remain open.
- Each method must call `conn.close()` before returning, even on exception paths (use try/finally or context manager).
- Transaction atomicity: wrap multi-statement operations in a single connection with explicit commit/rollback.

## File: src/inventory/database.py
```python
import sqlite3
from typing import Optional, List
from src.inventory.models import Product, StockMovement

class InventoryDB:
    """SQLite-backed inventory database. Constructor takes a database file path."""
    
    def __init__(self, db_path: str):
        """Initialize with a database file path. Creates tables on construction.
        Use a temporary connection that is CLOSED before returning."""
        ...
    
    def create_product(self, sku: str, name: str, quantity: int, price_cents: int) -> Product:
        """Insert a new product. Raises ValueError if SKU already exists."""
        ...
    
    def get_product(self, product_id: int) -> Optional[Product]:
        """Return Product by ID, or None if not found."""
        ...
    
    def get_product_by_sku(self, sku: str) -> Optional[Product]:
        """Return Product by SKU, or None if not found."""
        ...
    
    def list_products(self) -> List[Product]:
        """Return all products ordered by ID."""
        ...
    
    def update_quantity(self, product_id: int, new_quantity: int) -> Product:
        """Update product quantity. Raises ValueError if product not found."""
        ...
    
    def get_movements(self, product_id: int) -> List[StockMovement]:
        """Return all stock movements for a product, ordered by creation time."""
        ...
    
    def record_movement(self, product_id: int, delta: int, reason: str) -> StockMovement:
        """Record a stock movement. Raises ValueError if product not found."""
        ...
```

## File: src/inventory/models.py
```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class Product:
    id: int
    sku: str
    name: str
    quantity: int
    price_cents: int
    created_at: str   # ISO 8601 string, NOT datetime
    updated_at: str   # ISO 8601 string, NOT datetime
    
    @property
    def price_dollars(self) -> float:
        """Price in dollars (cents / 100)."""
        return self.price_cents / 100.0

@dataclass
class StockMovement:
    id: int
    product_id: int
    delta: int
    reason: Optional[str]
    created_at: str   # ISO 8601 string, NOT datetime
```

## File: src/inventory/service.py
```python
from typing import Optional, List
from src.inventory.database import InventoryDB
from src.inventory.models import Product, StockMovement

class InventoryService:
    """Business logic layer. Constructor takes an InventoryDB instance."""
    
    def __init__(self, db: InventoryDB):
        ...
    
    def add_product(self, sku: str, name: str, quantity: int, price_cents: int) -> Product:
        """Create a new product. Raises ValueError on duplicate SKU."""
        ...
    
    def get_product(self, product_id: int) -> Optional[Product]:
        """Get product by ID. Returns None if not found."""
        ...
    
    def get_product_by_sku(self, sku: str) -> Optional[Product]:
        """Get product by SKU. Returns None if not found."""
        ...
    
    def list_products(self) -> List[Product]:
        """List all products ordered by ID."""
        ...
    
    def receive_stock(self, product_id: int, quantity: int, reason: str) -> Product:
        """Receive stock. Raises ValueError if product not found or quantity <= 0."""
        ...
    
    def ship_stock(self, product_id: int, quantity: int, reason: str) -> Product:
        """Ship stock. Raises ValueError if product not found, quantity <= 0, or insufficient stock."""
        ...
    
    def adjust_stock(self, product_id: int, new_quantity: int, reason: str) -> Product:
        """Adjust stock to exact quantity. Raises ValueError if product not found or new_quantity < 0."""
        ...
    
    def get_history(self, product_id: int) -> List[StockMovement]:
        """Get stock movement history. Returns empty list if product not found."""
        ...
```

## File: src/inventory/api.py
```python
# FastAPI REST API — optional, not tested by acceptance suite
# Must not conflict with the above interfaces.
```

## IMMUTABLE ACCEPTANCE TESTS
tests/test_inventory.py — 20 test functions, 21 assertions across 4 test classes:
- TestProductCRUD: 6 tests (create, duplicate, get by id, get by sku, nonexistent, list)
- TestStockOperations: 7 tests (receive, ship, insufficient, adjust up, adjust down, adjust zero, adjust negative)
- TestStockHistory: 4 tests (history receive, history ship, multiple operations, nonexistent product)
- TestProductModel: 3 tests (price dollars, price dollars exact, timestamps)

Total: 20 tests, 21 assertions (test_history_multiple_operations has 4 assertions).

## Transaction Integrity
- Stock movements must be recorded atomically with quantity updates.
- Use a single connection for the record_movement + update_quantity sequence in service methods.
- Or use separate connections with explicit ordering (record first, then update).
- Ensure no partial state if an operation fails midway.
