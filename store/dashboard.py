from decimal import Decimal

from flask import Blueprint
from sqlalchemy import func

from .common import get_current_user, response
from .extensions import db
from .models import Order, Product


bp = Blueprint("dashboard", __name__, url_prefix="/api")


@bp.get("/dashboard")
def dashboard():
    user = get_current_user()
    conditions = [] if user.role == "admin" else [Order.user_id == user.id]
    by_status = dict(db.session.query(Order.status, func.count(Order.id)).filter(*conditions).group_by(Order.status).all())
    paid = db.session.query(func.coalesce(func.sum(Order.total_amount), 0)).filter(
        *conditions, Order.status.in_(["paid", "shipped", "completed"])).scalar()
    data = {"order_count": sum(by_status.values()), "pending_count": by_status.get("pending", 0),
            "paid_amount": str(Decimal(paid).quantize(Decimal("0.01"))), "by_status": by_status}
    if user.role == "admin":
        data["product_count"] = Product.query.count()
        data["low_stock"] = [p.to_dict() for p in Product.query.filter(
            Product.is_active.is_(True), Product.stock < 10).order_by(Product.stock, Product.id).limit(6)]
    return response(data)
