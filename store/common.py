import re
import secrets
from decimal import Decimal, InvalidOperation

from flask import jsonify, request, session

from .extensions import db
from .models import User


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def response(data, status=200):
    return jsonify(data=data, csrf_token=csrf_token()), status


def json_body(allowed):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise APIError("请提交 JSON 对象")
    extra = set(data) - set(allowed)
    if extra:
        raise APIError("包含不支持的字段：" + "、".join(sorted(extra)))
    return data


def string_field(data, key, label, maximum, required=True):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise APIError(f"{label}必须是字符串")
    value = value.strip()
    if required and not value:
        raise APIError(f"{label}不能为空")
    if len(value) > maximum:
        raise APIError(f"{label}不能超过 {maximum} 个字符")
    return value


def integer(value, label, minimum=1, maximum=100000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise APIError(f"{label}必须是 {minimum} 至 {maximum} 的整数")
    return value


def money(value):
    if type(value) not in (str, int, float):
        raise APIError("价格格式不正确")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not Decimal("0.01") <= result <= Decimal("1000000"):
            raise APIError("价格必须在 0.01 至 1000000 元之间")
        if result != result.quantize(Decimal("0.01")):
            raise APIError("价格最多保留两位小数")
        return result.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise APIError("价格格式不正确") from None


def username(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", value):
        raise APIError("用户名需为 3 至 32 位英文字母、数字、下划线或短横线")
    return value.lower()


def get_current_user():
    """读取登录用户；路由直接调用它，不使用自定义装饰器。"""
    user_id = session.get("user_id")
    if user_id is None:
        raise APIError("请先登录", 401)
    user = db.session.get(User, user_id)
    if user is None:
        session.clear()
        raise APIError("登录状态已失效，请重新登录", 401)
    return user


def page_args():
    try:
        page = int(request.args.get("page", "1"))
        size = int(request.args.get("page_size", "12"))
    except ValueError:
        raise APIError("分页参数必须是整数") from None
    if not 1 <= page <= 100000 or not 1 <= size <= 100:
        raise APIError("页码需为正整数，每页条数为 1 至 100")
    return page, size


def paginate(query):
    page, size = page_args()
    result = query.paginate(page=page, per_page=size, error_out=False)
    return {"items": [item.to_dict() for item in result.items], "total": result.total,
            "page": page, "page_size": size, "pages": result.pages}
