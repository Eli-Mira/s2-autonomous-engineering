import sqlite3
from typing import Optional, List

from .models import Product, StockMovement


class InventoryDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    quantity INTEGER DEFAULT 0,
                    price_cents INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    delta INTEGER NOT NULL,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_product(row) -> Product:
        return Product(
            id=row["id"],
            sku=row["sku"],
            name=row["name"],
            quantity=row["quantity"],
            price_cents=row["price_cents"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_movement(row) -> StockMovement:
        return StockMovement(
            id=row["id"],
            product_id=row["product_id"],
            delta=row["delta"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def create_product(self, sku: str, name: str, quantity: int, price_cents: int) -> Product:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO products (sku, name, quantity, price_cents) VALUES (?, ?, ?, ?)",
                (sku, name, quantity, price_cents),
            )
            conn.commit()
            product_id = cursor.lastrowid
            return self.get_product(product_id)
        except sqlite3.IntegrityError:
            raise ValueError(f"Product with SKU {sku} already exists")
        finally:
            conn.close()

    def get_product(self, product_id: int) -> Optional[Product]:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            return self._row_to_product(row) if row else None
        finally:
            conn.close()

    def get_product_by_sku(self, sku: str) -> Optional[Product]:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
            return self._row_to_product(row) if row else None
        finally:
            conn.close()

    def list_products(self) -> List[Product]:
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
            return [self._row_to_product(r) for r in rows]
        finally:
            conn.close()

    def update_quantity(self, product_id: int, new_quantity: int) -> Product:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if not row:
                raise ValueError("Product not found")
            conn.execute(
                "UPDATE products SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_quantity, product_id),
            )
            conn.commit()
            return self.get_product(product_id)
        finally:
            conn.close()

    def record_movement(self, product_id: int, delta: int, reason: str) -> StockMovement:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
            if not row:
                raise ValueError("Product not found")
            cursor = conn.execute(
                "INSERT INTO stock_movements (product_id, delta, reason) VALUES (?, ?, ?)",
                (product_id, delta, reason),
            )
            conn.commit()
            movement_id = cursor.lastrowid
            return self.get_movement(movement_id)
        finally:
            conn.close()

    def get_movement(self, movement_id: int) -> StockMovement:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM stock_movements WHERE id = ?", (movement_id,)).fetchone()
            return self._row_to_movement(row)
        finally:
            conn.close()

    def get_movements(self, product_id: int) -> List[StockMovement]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM stock_movements WHERE product_id = ? ORDER BY created_at",
                (product_id,),
            ).fetchall()
            return [self._row_to_movement(r) for r in rows]
        finally:
            conn.close()
