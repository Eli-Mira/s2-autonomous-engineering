from typing import Optional, List

from .database import InventoryDB
from .models import Product, StockMovement


class InventoryService:
    def __init__(self, db: InventoryDB):
        self.db = db

    def add_product(self, sku: str, name: str, quantity: int, price_cents: int) -> Product:
        return self.db.create_product(sku, name, quantity, price_cents)

    def get_product(self, product_id: int) -> Optional[Product]:
        return self.db.get_product(product_id)

    def get_product_by_sku(self, sku: str) -> Optional[Product]:
        return self.db.get_product_by_sku(sku)

    def list_products(self) -> List[Product]:
        return self.db.list_products()

    def receive_stock(self, product_id: int, quantity: int, reason: str) -> Product:
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
        product = self.db.get_product(product_id)
        if product is None:
            raise ValueError("Product not found")
        new_quantity = product.quantity + quantity
        self.db.update_quantity(product_id, new_quantity)
        self.db.record_movement(product_id, quantity, reason)
        return self.db.get_product(product_id)

    def ship_stock(self, product_id: int, quantity: int, reason: str) -> Product:
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
        product = self.db.get_product(product_id)
        if product is None:
            raise ValueError("Product not found")
        if product.quantity < quantity:
            raise ValueError("Insufficient stock")
        new_quantity = product.quantity - quantity
        self.db.update_quantity(product_id, new_quantity)
        self.db.record_movement(product_id, -quantity, reason)
        return self.db.get_product(product_id)

    def adjust_stock(self, product_id: int, new_quantity: int, reason: str) -> Product:
        if new_quantity < 0:
            raise ValueError("Quantity cannot be negative")
        product = self.db.get_product(product_id)
        if product is None:
            raise ValueError("Product not found")
        delta = new_quantity - product.quantity
        self.db.update_quantity(product_id, new_quantity)
        if delta != 0:
            self.db.record_movement(product_id, delta, reason)
        return self.db.get_product(product_id)

    def get_history(self, product_id: int) -> List[StockMovement]:
        return self.db.get_movements(product_id)
