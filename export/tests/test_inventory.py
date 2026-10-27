"""Acceptance tests for Inventory Service - PROTECTED, do not modify."""

import pytest
import tempfile
import os
from datetime import datetime

from src.inventory.database import InventoryDB
from src.inventory.service import InventoryService
from src.inventory.models import Product, StockMovement


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    os.unlink(db_path)


@pytest.fixture
def db(temp_db):
    return InventoryDB(temp_db)


@pytest.fixture
def service(db):
    return InventoryService(db)


class TestProductCRUD:
    """Test product create, read, update, delete operations."""
    
    def test_create_product(self, service):
        """Test creating a new product."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        assert product.id is not None
        assert product.sku == "SKU001"
        assert product.name == "Widget A"
        assert product.quantity == 10
        assert product.price_cents == 2999
        assert product.price_dollars == 29.99
    
    def test_create_duplicate_sku_fails(self, service):
        """Test that duplicate SKU raises error."""
        service.add_product("SKU001", "Widget A", 10, 2999)
        with pytest.raises(ValueError, match="already exists"):
            service.add_product("SKU001", "Widget B", 5, 1999)
    
    def test_get_product_by_id(self, service):
        """Test retrieving product by ID."""
        created = service.add_product("SKU001", "Widget A", 10, 2999)
        product = service.get_product(created.id)
        assert product is not None
        assert product.id == created.id
        assert product.sku == "SKU001"
    
    def test_get_product_by_sku(self, service):
        """Test retrieving product by SKU."""
        service.add_product("SKU001", "Widget A", 10, 2999)
        product = service.get_product_by_sku("SKU001")
        assert product is not None
        assert product.sku == "SKU001"
    
    def test_get_nonexistent_product_returns_none(self, service):
        """Test that nonexistent product returns None."""
        assert service.get_product(999) is None
        assert service.get_product_by_sku("NONEXISTENT") is None
    
    def test_list_products(self, service):
        """Test listing all products."""
        service.add_product("SKU001", "Widget A", 10, 2999)
        service.add_product("SKU002", "Widget B", 5, 1999)
        products = service.list_products()
        assert len(products) == 2
        assert products[0].sku == "SKU001"
        assert products[1].sku == "SKU002"


class TestStockOperations:
    """Test stock receive, ship, adjust operations."""
    
    def test_receive_stock(self, service):
        """Test receiving stock increases quantity."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        updated = service.receive_stock(product.id, 5, "purchase_order")
        assert updated.quantity == 15
    
    def test_ship_stock(self, service):
        """Test shipping stock decreases quantity."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        updated = service.ship_stock(product.id, 3, "order_123")
        assert updated.quantity == 7
    
    def test_ship_insufficient_stock_fails(self, service):
        """Test that shipping more than available fails."""
        product = service.add_product("SKU001", "Widget A", 5, 2999)
        with pytest.raises(ValueError, match="Insufficient stock"):
            service.ship_stock(product.id, 10, "order_123")
    
    def test_adjust_stock_up(self, service):
        """Test adjusting stock to higher quantity."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        updated = service.adjust_stock(product.id, 20, "inventory_count")
        assert updated.quantity == 20
    
    def test_adjust_stock_down(self, service):
        """Test adjusting stock to lower quantity."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        updated = service.adjust_stock(product.id, 3, "damage_writeoff")
        assert updated.quantity == 3
    
    def test_adjust_to_zero(self, service):
        """Test adjusting stock to zero."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        updated = service.adjust_stock(product.id, 0, "clearance")
        assert updated.quantity == 0
    
    def test_adjust_negative_fails(self, service):
        """Test that adjusting to negative fails."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        with pytest.raises(ValueError, match="cannot be negative"):
            service.adjust_stock(product.id, -5, "error")


class TestStockHistory:
    """Test stock movement history tracking."""
    
    def test_history_records_receive(self, service):
        """Test that receive creates history entry."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        service.receive_stock(product.id, 5, "purchase_order")
        history = service.get_history(product.id)
        assert len(history) == 1
        assert history[0].delta == 5
        assert history[0].reason == "purchase_order"
    
    def test_history_records_ship(self, service):
        """Test that ship creates history entry."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        service.ship_stock(product.id, 3, "order_123")
        history = service.get_history(product.id)
        assert len(history) == 1
        assert history[0].delta == -3
        assert history[0].reason == "order_123"
    
    def test_history_multiple_operations(self, service):
        """Test multiple operations create multiple history entries."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        service.receive_stock(product.id, 5, "purchase")
        service.ship_stock(product.id, 2, "order")
        service.adjust_stock(product.id, 20, "count")
        history = service.get_history(product.id)
        assert len(history) == 3
        assert history[0].delta == 5
        assert history[1].delta == -2
        assert history[2].delta == 7  # 20 - 13 = 7
    
    def test_history_nonexistent_product(self, service):
        """Test history for nonexistent product returns empty list."""
        history = service.get_history(999)
        assert history == []


class TestProductModel:
    """Test Product model properties."""
    
    def test_price_dollars_conversion(self, service):
        """Test price conversion from cents to dollars."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        assert product.price_dollars == 29.99
    
    def test_price_dollars_exact(self, service):
        """Test price conversion for exact dollar amounts."""
        product = service.add_product("SKU001", "Widget A", 10, 1000)
        assert product.price_dollars == 10.00
    
    def test_created_updated_timestamps(self, service):
        """Test that timestamps are set."""
        product = service.add_product("SKU001", "Widget A", 10, 2999)
        assert product.created_at is not None
        assert product.updated_at is not None
        # Should be valid ISO format
        datetime.fromisoformat(product.created_at)
        datetime.fromisoformat(product.updated_at)
