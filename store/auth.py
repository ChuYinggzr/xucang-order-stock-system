from flask import Blueprint, session
from werkzeug.security import check_password_hash, generate_password_hash

from .common import get_current_user, APIError, json_body, response, string_field, username
from .extensions import db
from .models import User


bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def credentials():
    data = json_body({"username", "password"})
    name = username(string_field(data, "username", "用户名", 32))
    password = data.get("password")
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise APIError("密码需为 8 至 128 个字符")
    return name, password


@bp.get("/session")
def current_session():
    user = db.session.get(User, session["user_id"]) if session.get("user_id") else None
    return response({"user": user.to_dict() if user else None})


@bp.post("/register")
def register():
    name, password = credentials()
    if User.query.filter_by(username=name).first():
        raise APIError("用户名已存在", 409)
    user = User(username=name, password_hash=generate_password_hash(password), role="customer")
    db.session.add(user)
    db.session.commit()
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return response({"user": user.to_dict()}, 201)


@bp.post("/login")
def login():
    name, password = credentials()
    user = User.query.filter_by(username=name).first()
    if user is None or not check_password_hash(user.password_hash, password):
        raise APIError("用户名或密码不正确", 401)
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return response({"user": user.to_dict()})


@bp.post("/logout")
def logout():
    user = get_current_user()
    session.clear()
    return response({"user": None})
