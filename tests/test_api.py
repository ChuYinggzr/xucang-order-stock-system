import tempfile
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier

from werkzeug.security import generate_password_hash

test_directory = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(test_directory.name) / "tests.db").as_posix()
os.environ["SECRET_KEY"] = "test-secret"
os.environ["SESSION_COOKIE_SECURE"] = "0"

from app import app
from store.extensions import db
from store.models import Order, Product, StockMovement, User


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            for name, role in (("admin", "admin"), ("alice", "customer"), ("bobby", "customer")):
                db.session.add(User(username=name, role=role, password_hash=generate_password_hash("password123", method="pbkdf2:sha256:1000")))
            db.session.add_all([Product(sku="A", name="商品A", description="", price=Decimal("12.50"), stock=10),
                                Product(sku="B", name="商品B", description="", price=Decimal("20.00"), stock=1)])
            db.session.commit()
        self.admin = self.login("admin")
        self.alice = self.login("alice")
        self.bobby = self.login("bobby")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        with self.app.app_context():
            db.drop_all()
            db.engine.dispose()

    def login(self, name):
        client = self.app.test_client()
        result = self.send(client, "POST", "/api/auth/login", {"username": name, "password": "password123"})
        self.assertEqual(result.status_code, 200)
        return client

    def send(self, client, method, path, body=None):
        token = client.get("/api/auth/session").json["csrf_token"]
        return client.open(path, method=method, json={} if body is None else body, headers={"X-CSRF-Token": token})

    def order(self, client=None, items=None, key="request_0001", **extra):
        return self.send(client or self.alice, "POST", "/api/orders", {
            "items": items or [{"product_id": 1, "quantity": 2}], "note": "测试订单", "request_key": key, **extra})

    def stock(self, pid=1):
        with self.app.app_context():
            return db.session.get(Product, pid).stock

    def test_home_and_static_files(self):
        c = self.app.test_client()
        self.assertEqual(c.get("/").status_code, 200)
        with c.get("/static/app.js") as result:
            self.assertEqual(result.status_code, 200)
        self.assertIn("default-src", c.get("/").headers["Content-Security-Policy"])

    def test_register_login_logout(self):
        c = self.app.test_client()
        r = self.send(c, "POST", "/api/auth/register", {"username": "New_User", "password": "password123"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json["data"]["user"]["role"], "customer")
        self.assertEqual(r.json["data"]["user"]["username"], "new_user")
        with self.app.app_context():
            self.assertNotEqual(User.query.filter_by(username="new_user").first().password_hash, "password123")
        self.assertEqual(self.send(c, "POST", "/api/auth/logout").status_code, 200)
        self.assertEqual(c.get("/api/orders").status_code, 401)

    def test_duplicate_username_and_role_escalation(self):
        c = self.app.test_client()
        self.assertEqual(self.send(c, "POST", "/api/auth/register", {"username":"alice", "password":"password123"}).status_code, 409)
        self.assertEqual(self.send(c, "POST", "/api/auth/register", {"username":"hacker", "password":"password123", "role":"admin"}).status_code, 400)

    def test_bad_password_and_csrf(self):
        c = self.app.test_client()
        self.assertEqual(self.send(c,"POST","/api/auth/login",{"username":"alice","password":"wrong_password"}).status_code,401)
        self.assertEqual(self.alice.post("/api/orders",json={}).status_code,403)

    def test_customer_cannot_manage_stock(self):
        self.assertEqual(self.send(self.alice,"POST","/api/products",{}).status_code,403)
        self.assertEqual(self.send(self.alice,"POST","/api/products/1/stock",{"delta":3,"note":"补货"}).status_code,403)
        self.assertEqual(self.alice.get("/api/stock-movements").status_code,403)

    def test_product_create_edit_and_duplicate_sku(self):
        data={"sku":"C","name":"新商品","price":"19.99","stock":5}
        r=self.send(self.admin,"POST","/api/products",data)
        self.assertEqual(r.status_code,201)
        pid=r.json["data"]["id"]
        self.assertEqual(self.send(self.admin,"POST","/api/products",data).status_code,409)
        self.assertEqual(self.send(self.admin,"PATCH",f"/api/products/{pid}",{"name":"改名","price":"25.00","is_active":False}).status_code,200)
        self.assertEqual(self.alice.get("/api/products?q=改名").json["data"]["total"],0)

    def test_money_and_required_fields_validation(self):
        for price in ("NaN","Infinity","0","-1","1.001",True,None):
            with self.subTest(price=price):
                r=self.send(self.admin,"POST","/api/products",{"sku":"C","name":"商品","price":price})
                self.assertEqual(r.status_code,400)
        self.assertEqual(self.send(self.admin,"POST","/api/products",{"sku":"   ","name":"商品","price":"10"}).status_code,400)

    def test_stock_adjustment_and_history(self):
        self.assertEqual(self.send(self.admin,"POST","/api/products/1/stock",{"delta":4,"note":"采购补货"}).status_code,200)
        self.assertEqual(self.stock(),14)
        self.assertEqual(self.send(self.admin,"POST","/api/products/1/stock",{"delta":-15,"note":"盘点"}).status_code,409)
        self.assertEqual(self.stock(),14)
        self.assertEqual(self.admin.get("/api/stock-movements?product_id=1").json["data"]["total"],1)

    def test_order_amount_and_stock(self):
        r=self.order(items=[{"product_id":1,"quantity":2},{"product_id":2,"quantity":1}])
        self.assertEqual(r.status_code,201)
        self.assertEqual(r.json["data"]["total_amount"],"45.00")
        self.assertEqual(self.stock(),8)
        self.assertEqual(self.stock(2),0)

    def test_multi_product_failure_rolls_back_everything(self):
        r=self.order(items=[{"product_id":1,"quantity":2},{"product_id":2,"quantity":2}])
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.stock(),10)
        with self.app.app_context():
            self.assertEqual(Order.query.count(),0)
            self.assertEqual(StockMovement.query.count(),0)

    def test_cancel_is_idempotent_and_restores_stock_once(self):
        oid=self.order().json["data"]["id"]
        for _ in range(2):
            self.assertEqual(self.send(self.alice,"POST",f"/api/orders/{oid}/cancel").status_code,200)
        self.assertEqual(self.stock(),10)
        with self.app.app_context():
            self.assertEqual(StockMovement.query.filter_by(reason="cancel").count(),1)

    def test_order_access_isolation(self):
        oid=self.order().json["data"]["id"]
        for suffix in ("", "/cancel", "/pay", "/complete"):
            r=self.bobby.get(f"/api/orders/{oid}") if not suffix else self.send(self.bobby,"POST",f"/api/orders/{oid}{suffix}")
            self.assertEqual(r.status_code,404)
        self.assertEqual(self.bobby.get("/api/orders").json["data"]["total"],0)
        self.assertEqual(self.admin.get(f"/api/orders/{oid}").status_code,200)

    def test_order_price_and_name_snapshot(self):
        oid=self.order().json["data"]["id"]
        self.send(self.admin,"PATCH","/api/products/1",{"name":"修改后的商品","price":"99.00"})
        order=self.alice.get(f"/api/orders/{oid}").json["data"]
        self.assertEqual(order["items"][0]["product_name"],"商品A")
        self.assertEqual(order["items"][0]["unit_price"],"12.50")
        self.assertEqual(order["total_amount"],"25.00")

    def test_cannot_buy_inactive_product(self):
        self.send(self.admin,"PATCH","/api/products/1",{"is_active":False})
        self.assertEqual(self.order().status_code,409)
        self.assertEqual(self.stock(),10)

    def test_state_flow_and_invalid_transitions(self):
        oid=self.order().json["data"]["id"]
        self.assertEqual(self.send(self.admin,"POST",f"/api/orders/{oid}/ship").status_code,409)
        self.assertEqual(self.send(self.alice,"POST",f"/api/orders/{oid}/pay").status_code,200)
        self.assertEqual(self.send(self.alice,"POST",f"/api/orders/{oid}/cancel").status_code,409)
        self.assertEqual(self.send(self.alice,"POST",f"/api/orders/{oid}/ship").status_code,403)
        self.assertEqual(self.send(self.admin,"POST",f"/api/orders/{oid}/ship").status_code,200)
        self.assertEqual(self.send(self.alice,"POST",f"/api/orders/{oid}/complete").status_code,200)
        self.assertEqual(self.stock(),8)

    def test_duplicate_submission_does_not_deduct_twice(self):
        first=self.order()
        second=self.order()
        self.assertEqual(first.status_code,201)
        self.assertEqual(second.status_code,200)
        self.assertEqual(first.json["data"]["id"],second.json["data"]["id"])
        self.assertEqual(self.stock(),8)
        self.assertEqual(self.order(items=[{"product_id":1,"quantity":3}]).status_code,409)

    def test_order_input_validation_and_client_price_rejected(self):
        for quantity in (0,-1,True,"2",1.5):
            self.assertEqual(self.order(items=[{"product_id":1,"quantity":quantity}]).status_code,400)
        self.assertEqual(self.order(total_amount="0.01").status_code,400)
        self.assertEqual(self.order(items=[{"product_id":1,"quantity":1},{"product_id":1,"quantity":2}]).status_code,400)
        self.assertEqual(self.stock(),10)

    def test_pagination_search_and_date_validation(self):
        self.order()
        for path in ("/api/products?page=0","/api/orders?page_size=101","/api/orders?from=bad","/api/orders?from=2026-10-13&to=2026-10-12"):
            self.assertEqual(self.alice.get(path).status_code,400)
        self.assertEqual(self.alice.get("/api/products?q=商品A").json["data"]["total"],1)
        self.assertEqual(self.alice.get("/api/orders?status=pending&page_size=1").json["data"]["total"],1)

    def test_statistics_are_scoped_and_cancelled_excluded_from_paid_amount(self):
        oid=self.order().json["data"]["id"]
        self.send(self.alice,"POST",f"/api/orders/{oid}/pay")
        second=self.order(key="request_0002").json["data"]["id"]
        self.send(self.alice,"POST",f"/api/orders/{second}/cancel")
        self.assertEqual(self.alice.get("/api/dashboard").json["data"]["paid_amount"],"25.00")
        self.assertEqual(self.bobby.get("/api/dashboard").json["data"]["order_count"],0)
        self.assertEqual(self.admin.get("/api/dashboard").json["data"]["order_count"],2)

    def parallel(self, tasks):
        barrier=Barrier(len(tasks))
        def work(task):
            barrier.wait(timeout=10)
            return task()
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            return list(executor.map(work,tasks))

    def test_concurrent_orders_compete_for_last_stock(self):
        results=self.parallel([lambda:self.order(self.alice,[{"product_id":2,"quantity":1}],"alice_order_1"),
                               lambda:self.order(self.bobby,[{"product_id":2,"quantity":1}],"bobby_order_1")])
        self.assertEqual(sorted(r.status_code for r in results),[201,409])
        self.assertEqual(self.stock(2),0)

    def test_concurrent_cancellation_returns_stock_once(self):
        oid=self.order().json["data"]["id"]
        second_client=self.login("alice")
        results=self.parallel([lambda:self.send(self.alice,"POST",f"/api/orders/{oid}/cancel"),
                               lambda:self.send(second_client,"POST",f"/api/orders/{oid}/cancel")])
        self.assertEqual([r.status_code for r in results],[200,200])
        self.assertEqual(self.stock(),10)
        with self.app.app_context():
            self.assertEqual(StockMovement.query.filter_by(reason="cancel").count(),1)

    def test_concurrent_duplicate_submission_creates_one_order(self):
        second_client=self.login("alice")
        results=self.parallel([lambda:self.order(self.alice),lambda:self.order(second_client)])
        self.assertEqual(sorted(r.status_code for r in results),[200,201])
        self.assertEqual(self.stock(),8)
        with self.app.app_context():
            self.assertEqual(Order.query.count(),1)

    def test_concurrent_duplicate_submission_for_last_item(self):
        second_client=self.login("alice")
        items=[{"product_id":2,"quantity":1}]
        results=self.parallel([lambda:self.order(self.alice,items),lambda:self.order(second_client,items)])
        self.assertEqual(sorted(r.status_code for r in results),[200,201])
        self.assertEqual(results[0].json["data"]["id"],results[1].json["data"]["id"])
        self.assertEqual(self.stock(2),0)

    def test_concurrent_pay_and_cancel_choose_one_transition(self):
        oid=self.order().json["data"]["id"]
        second_client=self.login("alice")
        results=self.parallel([lambda:self.send(self.alice,"POST",f"/api/orders/{oid}/pay"),
                               lambda:self.send(second_client,"POST",f"/api/orders/{oid}/cancel")])
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        with self.app.app_context():
            order=db.session.get(Order,oid)
            self.assertEqual(self.stock(),10 if order.status=="cancelled" else 8)


if __name__ == "__main__":
    unittest.main()
