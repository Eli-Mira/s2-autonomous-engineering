from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from decimal import Decimal
from typing import List, Optional

from .database import init_db, get_connection
from .service import InventoryService

app = FastAPI(title="Inventory Service")
service = InventoryService()

# Initialize database on startup
@app.on_event("startup")
def startup():
    conn = get_connection()
    try:
        init_db(conn)
    finally:
        conn.close()


# Pydantic Models for Request/Response
class ProductCreate(BaseModel):
    name: str
    sku: str
    price: Decimal

class ProductResponse(BaseModel):
    id: int
    name: str
    sku: str
    price: Decimal
    stock_quantity: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

class MovementCreate(BaseModel):
    quantity: int
    reason: Optional[str] = None

class MovementResponse(BaseModel):
    id: int
    product_id: int
    movement_type: str
    quantity: int
    reason: Optional[str]
    timestamp: str

    class Config:
        from_attributes = True


@app.post("/products", response_model=ProductResponse, status_code=201)
def create_product(product_in: ProductCreate):
    try:
        product = service.create_product(
            name=product_in.name,
            sku=product_in.sku,
            price=product_in.price
        )
        return _product_to_response(product)
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="SKU already exists")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int):
    product = service.get_product_by_id(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return _product_to_response(product)

@app.get("/products/sku/{sku}", response_model=ProductResponse)
def get_product_by_sku(sku: str):
    product = service.get_product_by_sku(sku)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return _product_to_response(product)

@app.get("/products", response_model=List[ProductResponse])
def list_products():
    products = service.list_products()
    return [_product_to_response(p) for p in products]

@app.post("/products/{product_id}/stock/receive", response_model=MovementResponse)
def receive_stock(product_id: int, movement_in: MovementCreate):
    try:
        movement = service.receive_stock(
            product_id=product_id,
            quantity=movement_in.quantity,
            reason=movement_in.reason
        )
        return _movement_to_response(movement)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/products/{product_id}/stock/ship", response_model=MovementResponse)
def ship_stock(product_id: int, movement_in: MovementCreate):
    try:
        movement = service.ship_stock(
            product_id=product_id,
            quantity=movement_in.quantity,
            reason=movement_in.reason
        )
        return _movement_to_response(movement)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/products/{product_id}/stock/adjust", response_model=MovementResponse)
def adjust_stock(product_id: int, movement_in: MovementCreate):
    try:
        movement = service.adjust_stock(
            product_id=product_id,
            quantity=movement_in.quantity,
            reason=movement_in.reason
        )
        return _movement_to_response(movement)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/products/{product_id}/stock/history", response_model=List[MovementResponse])
def get_stock_history(product_id: int):
    movements = service.get_stock_history(product_id)
    return [_movement_to_response(m) for m in movements]


def _product_to_response(product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku,
        "price": product.price,
        "stock_quantity": product.stock_quantity,
        "created_at": product.created_at.isoformat(),
        "updated_at": product.updated_at.isoformat()
    }

def _movement_to_response(movement) -> dict:
    return {
        "id": movement.id,
        "product_id": movement.product_id,
        "movement_type": movement.movement_type,
        "quantity": movement.quantity,
        "reason": movement.reason,
        "timestamp": movement.timestamp.isoformat()
    }
