import os
import secrets
from datetime import timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from urllib.parse import quote_plus
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import generate_password_hash

from store.common import APIError, csrf_token
from store.extensions import db, migrate


BASE_DIR = Path(__file__).resolve().parent


load_dotenv(BASE_DIR / ".env")
app = Flask(__name__, template_folder="templates", static_folder="static",
            instance_path=str(BASE_DIR / "instance"))
Path(app.instance_path).mkdir(parents=True, exist_ok=True)
secret = os.getenv("SECRET_KEY")
if not secret:
    secret_path = Path(app.instance_path) / ".session-key"
    if not secret_path.exists():
        try:
            with secret_path.open("x", encoding="utf-8") as output:
                output.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    secret = secret_path.read_text(encoding="utf-8").strip()
database = os.getenv("DATABASE_URL")
if not database and os.getenv("USE_MYSQL", "0") == "1":
    db_user = quote_plus(os.getenv("DB_USER", "root"))
    db_password = quote_plus(os.getenv("DB_PASSWORD", ""))
    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = os.getenv("DB_PORT", "3306")
    db_name = os.getenv("DB_NAME", "order_stock")
    database = (
        f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}"
        f"/{db_name}?charset=utf8mb4"
    )
app.config.update(SECRET_KEY=secret, SQLALCHEMY_DATABASE_URI=database or "sqlite:///order_stock.db",
                  SQLALCHEMY_TRACK_MODIFICATIONS=False, SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
                  SESSION_COOKIE_NAME="order_stock_session", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE") == "1",
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=64 * 1024)
app.json.ensure_ascii = False
db.init_app(app)
migrate.init_app(app, db, render_as_batch=True)
from store import auth, dashboard, models, orders, products
for blueprint in (auth.bp, dashboard.bp, orders.bp, products.bp):
    app.register_blueprint(blueprint)

@app.before_request
def protect_mutations():
    if request.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            raise APIError("页面凭证已过期，请刷新后重试", 403)

@app.after_request
def response_headers(result):
    result.headers["X-Content-Type-Options"] = "nosniff"
    result.headers["X-Frame-Options"] = "DENY"
    result.headers["Referrer-Policy"] = "same-origin"
    result.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'"
    if request.path.startswith("/api/"):
        result.headers["Cache-Control"] = "no-store"
    return result

@app.get("/")
def index():
    return render_template("index.html")

@app.errorhandler(APIError)
def api_error(error):
    db.session.rollback()
    return jsonify(error={"message": error.message}, csrf_token=csrf_token()), error.status

@app.errorhandler(IntegrityError)
def integrity_error(error):
    db.session.rollback()
    return jsonify(error={"message": "数据冲突，请检查唯一编号、库存或订单状态"}, csrf_token=csrf_token()), 409

@app.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    app.logger.error("数据库操作失败：%s", type(error).__name__)
    return jsonify(error={"message": "数据库操作失败，本次修改已回滚"}, csrf_token=csrf_token()), 500

@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
def http_error(error):
    return jsonify(error={"message": {404: "地址不存在", 405: "请求方法不支持", 413: "请求内容过大"}[error.code]}), error.code

@app.cli.command("seed-demo")
def seed_demo():
    """首次创建演示账号及商品；再次执行不会覆盖已有数据。"""
    accounts = []
    admin = models.User.query.filter_by(username="demo_admin").first()
    for name, role in (("demo_admin", "admin"), ("demo_member", "customer")):
        existing = models.User.query.filter_by(username=name).first()
        if existing:
            if name == "demo_admin":
                admin = existing
            continue
        password = secrets.token_urlsafe(12)
        user = models.User(username=name, role=role, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()
        if role == "admin":
            admin = user
        accounts.append(f"{name} / {password} ({'管理员' if role == 'admin' else '普通用户'})")
    samples = [("OS-001", "随行保温杯", "通勤与学习时使用的轻便保温杯", "69.00", 36),
               ("OS-002", "方格笔记本", "记录课程、代码思路和每日计划", "18.50", 80),
               ("OS-003", "桌面收纳盒", "整理桌面的笔、线材和小工具", "32.00", 8),
               ("OS-004", "编织数据线", "一米长度，适合日常充电与传输", "29.90", 24),
               ("OS-005", "阅读台灯", "为阅读和晚间学习提供桌面照明", "129.00", 15),
               ("OS-006", "帆布手提袋", "装下书本、笔记本与日常随身物品", "39.00", 5)]
    for sku, name, description, price, stock in samples:
        if models.Product.query.filter_by(sku=sku).first():
            continue
        product = models.Product(sku=sku, name=name, description=description, price=price, stock=stock)
        db.session.add(product)
        db.session.flush()
        db.session.add(models.StockMovement(product_id=product.id, operator_id=admin.id,
                                            delta=stock, reason="initial", note="演示商品初始入库"))
    db.session.commit()
    if accounts:
        with (BASE_DIR / "demo_accounts.txt").open("a", encoding="utf-8") as output:
            output.write("\n".join(accounts) + "\n")
    click.echo("演示数据已准备好。账号信息保存在项目目录 demo_accounts.txt；已有账号及数据未被重置。")



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5010, debug=False)
