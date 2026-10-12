from datetime import datetime, timezone

from .extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value):
    return value.isoformat(timespec="seconds") + "Z" if value else None


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="customer")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    __table_args__ = (db.CheckConstraint("role IN ('customer','admin')", name="ck_user_role"),)

    def to_dict(self):
        return {"id": self.id, "username": self.username, "role": self.role}


class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    price = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.BigInteger, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    __table_args__ = (
        db.CheckConstraint("price > 0", name="ck_product_price"),
        db.CheckConstraint("stock >= 0", name="ck_product_stock"),
    )

    def to_dict(self):
        return {"id": self.id, "sku": self.sku, "name": self.name,
                "description": self.description, "price": str(self.price),
                "stock": self.stock, "is_active": self.is_active}


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(40), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    request_key = db.Column(db.String(64), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    total_amount = db.Column(db.Numeric(14, 2), nullable=False)
    note = db.Column(db.String(200), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, index=True)
    paid_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    user = db.relationship("User")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    __table_args__ = (
        db.UniqueConstraint("user_id", "request_key", name="uq_order_request"),
        db.CheckConstraint("status IN ('pending','paid','shipped','completed','cancelled')", name="ck_order_status"),
        db.CheckConstraint("total_amount > 0", name="ck_order_total"),
    )

    def to_dict(self):
        return {"id": self.id, "order_no": self.order_no, "user_id": self.user_id,
                "username": self.user.username, "status": self.status,
                "total_amount": str(self.total_amount), "note": self.note,
                "created_at": timestamp(self.created_at), "paid_at": timestamp(self.paid_at),
                "cancelled_at": timestamp(self.cancelled_at),
                "items": [item.to_dict() for item in self.items]}


class OrderItem(db.Model):
    __tablename__ = "order_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    product_name = db.Column(db.String(80), nullable=False)
    product_sku = db.Column(db.String(40), nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    subtotal = db.Column(db.Numeric(14, 2), nullable=False)
    order = db.relationship("Order", back_populates="items")
    __table_args__ = (
        db.UniqueConstraint("order_id", "product_id", name="uq_order_product"),
        db.CheckConstraint("quantity > 0 AND quantity <= 100000", name="ck_item_quantity"),
    )

    def to_dict(self):
        return {"product_id": self.product_id, "product_name": self.product_name,
                "product_sku": self.product_sku, "unit_price": str(self.unit_price),
                "quantity": self.quantity, "subtotal": str(self.subtotal)}


class StockMovement(db.Model):
    __tablename__ = "stock_movements"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), index=True)
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(16), nullable=False)
    note = db.Column(db.String(200), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    product = db.relationship("Product")
    operator = db.relationship("User")
    __table_args__ = (db.CheckConstraint("delta != 0", name="ck_movement_delta"),)

    def to_dict(self):
        return {"id": self.id, "product_id": self.product_id, "product_name": self.product.name,
                "order_id": self.order_id, "operator": self.operator.username,
                "delta": self.delta, "reason": self.reason, "note": self.note,
                "created_at": timestamp(self.created_at)}
