from flask import Blueprint, request
from sqlalchemy import or_, text

from .common import get_current_user, APIError, integer, json_body, money, paginate, response, string_field
from .extensions import db
from .models import Product, StockMovement


bp = Blueprint("products", __name__, url_prefix="/api")


@bp.get("/products")
def list_products():
    user = get_current_user()
    query = Product.query
    if user.role != "admin":
        query = query.filter_by(is_active=True)
    active = request.args.get("active", "")
    if active:
        if active not in {"1", "0"}:
            raise APIError("上架状态参数只能为 1 或 0")
        query = query.filter_by(is_active=active == "1")
    keyword = request.args.get("q", "").strip()
    if len(keyword) > 80:
        raise APIError("搜索内容不能超过 80 个字符")
    if keyword:
        query = query.filter(or_(Product.name.contains(keyword, autoescape=True), Product.sku.contains(keyword, autoescape=True)))
    return response(paginate(query.order_by(Product.id.desc())))


@bp.post("/products")
def create_product():
    user = get_current_user()
    if user.role != "admin":
        raise APIError("此操作需要管理员权限", 403)
    data = json_body({"sku", "name", "description", "price", "stock"})
    product = Product(sku=string_field(data, "sku", "SKU", 40),
                      name=string_field(data, "name", "商品名称", 80),
                      description=string_field(data, "description", "商品描述", 500, False),
                      price=money(data.get("price")), stock=integer(data.get("stock", 0), "初始库存", 0, 1000000))
    db.session.add(product)
    db.session.flush()
    if product.stock:
        db.session.add(StockMovement(product_id=product.id, operator_id=user.id,
                                     delta=product.stock, reason="initial", note="创建商品初始入库"))
    db.session.commit()
    return response(product.to_dict(), 201)


@bp.patch("/products/<int:product_id>")
def edit_product(product_id):
    user = get_current_user()
    if user.role != "admin":
        raise APIError("此操作需要管理员权限", 403)
    data = json_body({"name", "description", "price", "is_active"})
    if not data:
        raise APIError("请至少提交一个修改字段")
    product = db.session.get(Product, product_id)
    if product is None:
        raise APIError("商品不存在", 404)
    if "name" in data:
        product.name = string_field(data, "name", "商品名称", 80)
    if "description" in data:
        product.description = string_field(data, "description", "商品描述", 500, False)
    if "price" in data:
        product.price = money(data["price"])
    if "is_active" in data:
        if type(data["is_active"]) is not bool:
            raise APIError("上架状态必须是布尔值")
        product.is_active = data["is_active"]
    db.session.commit()
    return response(product.to_dict())


@bp.post("/products/<int:product_id>/stock")
def adjust_stock(product_id):
    user = get_current_user()
    if user.role != "admin":
        raise APIError("此操作需要管理员权限", 403)
    data = json_body({"delta", "note"})
    delta = integer(data.get("delta"), "库存增减量", -1000000, 1000000)
    if delta == 0:
        raise APIError("库存增减量不能为 0")
    note = string_field(data, "note", "调整原因", 200)
    if db.session.get(Product, product_id) is None:
        raise APIError("商品不存在", 404)
    result = db.session.execute(text(
        "UPDATE products SET stock = stock + :delta WHERE id = :product_id "
        "AND stock + :delta >= 0 AND stock + :delta <= :maximum"
    ), {"product_id": product_id, "delta": delta, "maximum": 1000000000})
    if result.rowcount != 1:
        raise APIError("调整后库存需在 0 至 1000000000 之间", 409)
    db.session.add(StockMovement(product_id=product_id, operator_id=user.id,
                                 delta=delta, reason="manual", note=note))
    db.session.commit()
    return response(db.session.get(Product, product_id).to_dict())


@bp.get("/stock-movements")
def stock_movements():
    user = get_current_user()
    if user.role != "admin":
        raise APIError("此操作需要管理员权限", 403)
    query = StockMovement.query
    value = request.args.get("product_id")
    if value is not None:
        try:
            product_id = int(value)
        except ValueError:
            raise APIError("商品编号格式不正确") from None
        integer(product_id, "商品编号", 1, 2147483647)
        query = query.filter_by(product_id=product_id)
    return response(paginate(query.order_by(StockMovement.id.desc())))
