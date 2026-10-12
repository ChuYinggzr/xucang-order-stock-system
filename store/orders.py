import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .common import get_current_user, APIError, integer, json_body, paginate, response, string_field
from .extensions import db
from .models import Order, OrderItem, Product, StockMovement, utc_now


bp = Blueprint("orders", __name__, url_prefix="/api/orders")
STATUSES = {"pending", "paid", "shipped", "completed", "cancelled"}


def accessible_order(order_id, user):
    query = Order.query.filter_by(id=order_id)
    if user.role != "admin":
        query = query.filter_by(user_id=user.id)
    order = query.first()
    if order is None:
        raise APIError("订单不存在或无权访问", 404)
    return order


def replay_order(key, fingerprint, user):
    order = Order.query.filter_by(user_id=user.id, request_key=key).first()
    if order is not None:
        if order.request_fingerprint != fingerprint:
            raise APIError("同一个提交编号不能用于不同的订单内容", 409)
        return response(order.to_dict())
    return None


@bp.post("")
def create_order():
    user = get_current_user()
    data = json_body({"items", "note", "request_key"})
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
        raise APIError("订单需包含 1 至 50 种商品")
    note = string_field(data, "note", "订单备注", 200, False)
    key = string_field(data, "request_key", "提交编号", 64)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", key):
        raise APIError("提交编号需为 8 至 64 位英文字母、数字、下划线或短横线")
    items = {}
    for item in raw_items:
        if not isinstance(item, dict) or set(item) != {"product_id", "quantity"}:
            raise APIError("商品项只能包含 product_id 和 quantity")
        pid = integer(item["product_id"], "商品编号", 1, 2147483647)
        quantity = integer(item["quantity"], "购买数量")
        if pid in items:
            raise APIError("同一种商品请合并数量后提交")
        items[pid] = quantity
    fingerprint = hashlib.sha256(json.dumps({"items": sorted(items.items()), "note": note},
                                            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    existing = replay_order(key, fingerprint, user)
    if existing is not None:
        return existing
    order = Order(order_no="OS" + uuid.uuid4().hex[:24].upper(), user_id=user.id,
                  request_key=key, request_fingerprint=fingerprint, total_amount=Decimal("0.00"), note=note)
    try:
        # 先计算金额，再插入订单，避免违反订单金额的数据库约束。
        snapshots = []
        total = Decimal("0.00")
        for pid in sorted(items):
            quantity = items[pid]
            product = Product.query.filter_by(id=pid).first()
            if product is None or not product.is_active:
                raise APIError("商品不存在或已下架", 409)
            # 用熟悉的参数化 SQL：库存足够且价格未变化时才扣减。
            result = db.session.execute(text(
                "UPDATE products SET stock = stock - :quantity "
                "WHERE id = :product_id AND is_active = :active "
                "AND stock >= :quantity AND price = :price"
            ), {"product_id": pid, "quantity": quantity, "active": True, "price": str(product.price)})
            if result.rowcount != 1:
                raise APIError(f"商品“{product.name}”库存不足或价格、上架状态已变化，请刷新后重试", 409)
            subtotal = product.price * quantity
            total += subtotal
            if total > Decimal("999999999999.99"):
                raise APIError("订单总金额过大，请拆分订单")
            snapshots.append((product, quantity, subtotal))
        order.total_amount = total
        db.session.add(order)
        db.session.flush()
        for product, quantity, subtotal in snapshots:
            db.session.add(OrderItem(order_id=order.id, product_id=product.id,
                                     product_name=product.name, product_sku=product.sku,
                                     unit_price=product.price, quantity=quantity, subtotal=subtotal))
            db.session.add(StockMovement(product_id=product.id, order_id=order.id,
                                         operator_id=user.id, delta=-quantity, reason="order", note="下单扣减"))
        db.session.commit()
    except (IntegrityError, APIError):
        db.session.rollback()
        # 并发重试可能先遇到库存不足，或被唯一约束拦截；回滚后重新查找原订单。
        existing = replay_order(key, fingerprint, user)
        if existing is not None:
            return existing
        raise
    return response(order.to_dict(), 201)


@bp.get("")
def list_orders():
    user = get_current_user()
    query = Order.query
    if user.role != "admin":
        query = query.filter_by(user_id=user.id)
    status = request.args.get("status", "")
    if status:
        if status not in STATUSES:
            raise APIError("订单状态不正确")
        query = query.filter_by(status=status)
    keyword = request.args.get("q", "").strip()
    if len(keyword) > 80:
        raise APIError("搜索内容不能超过 80 个字符")
    if keyword:
        query = query.filter(Order.order_no.contains(keyword, autoescape=True))
    dates = {}
    for name in ("from", "to"):
        value = request.args.get(name, "")
        if value:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                dates[name] = datetime.strptime(value, "%Y-%m-%d")
                if not 1970 <= dates[name].year <= 9998:
                    raise ValueError
            except ValueError:
                raise APIError("日期需使用 YYYY-MM-DD 格式，年份为 1970 至 9998") from None
    if dates.get("from") and dates.get("to") and dates["from"] > dates["to"]:
        raise APIError("开始日期不能晚于结束日期")
    if "from" in dates:
        query = query.filter(Order.created_at >= dates["from"])
    if "to" in dates:
        query = query.filter(Order.created_at < dates["to"] + timedelta(days=1))
    return response(paginate(query.order_by(Order.id.desc())))


@bp.get("/<int:order_id>")
def order_detail(order_id):
    user = get_current_user()
    return response(accessible_order(order_id, user).to_dict())


@bp.post("/<int:order_id>/cancel")
def cancel_order(order_id):
    user = get_current_user()
    json_body(set())
    order = accessible_order(order_id, user)
    if order.status == "cancelled":
        return response(order.to_dict())
    result = db.session.execute(text(
        "UPDATE orders SET status = :new_status, cancelled_at = :cancelled_at "
        "WHERE id = :order_id AND status = :old_status"
    ), {"order_id": order.id, "old_status": "pending", "new_status": "cancelled", "cancelled_at": utc_now().isoformat(sep=" ")})
    if result.rowcount != 1:
        db.session.rollback()
        latest = accessible_order(order_id, user)
        if latest.status == "cancelled":
            return response(latest.to_dict())
        raise APIError("只有待付款订单可以取消", 409)
    for item in sorted(order.items, key=lambda item: item.product_id):
        db.session.execute(text(
            "UPDATE products SET stock = stock + :quantity WHERE id = :product_id"
        ), {"product_id": item.product_id, "quantity": item.quantity})
        db.session.add(StockMovement(product_id=item.product_id, order_id=order.id,
                                     operator_id=user.id, delta=item.quantity, reason="cancel", note="取消订单归还"))
    db.session.commit()
    return response(db.session.get(Order, order_id).to_dict())


def transition(order_id, source, target, user):
    json_body(set())
    order = accessible_order(order_id, user)
    if order.status == target:
        return response(order.to_dict())
    if target == "paid":
        result = db.session.execute(text(
            "UPDATE orders SET status = :new_status, paid_at = :paid_at "
            "WHERE id = :order_id AND status = :old_status"
        ), {"order_id": order.id, "old_status": source, "new_status": target, "paid_at": utc_now().isoformat(sep=" ")})
    else:
        result = db.session.execute(text(
            "UPDATE orders SET status = :new_status WHERE id = :order_id AND status = :old_status"
        ), {"order_id": order.id, "old_status": source, "new_status": target})
    if result.rowcount != 1:
        db.session.rollback()
        latest = accessible_order(order_id, user)
        if latest.status == target:
            return response(latest.to_dict())
        raise APIError("当前订单状态不允许此操作", 409)
    db.session.commit()
    return response(db.session.get(Order, order_id).to_dict())


@bp.post("/<int:order_id>/pay")
def simulate_payment(order_id):
    user = get_current_user()
    return transition(order_id, "pending", "paid", user)


@bp.post("/<int:order_id>/ship")
def ship_order(order_id):
    user = get_current_user()
    if user.role != "admin":
        raise APIError("此操作需要管理员权限", 403)
    return transition(order_id, "paid", "shipped", user)


@bp.post("/<int:order_id>/complete")
def complete_order(order_id):
    user = get_current_user()
    return transition(order_id, "shipped", "completed", user)
