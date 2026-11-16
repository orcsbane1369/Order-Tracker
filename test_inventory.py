import os, sqlite3, tempfile, smtplib
TMP = tempfile.mkdtemp()

# ---------------- a database made BEFORE inventory existed upgrades without losing anything
OLD = os.path.join(TMP, "old.db")
con = sqlite3.connect(OLD)
con.executescript("""
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE categories(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', unit_price_c INTEGER NOT NULL DEFAULT 0, category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL);
INSERT INTO products(name, unit_price_c) VALUES('Old Tumbler', 3500);
""")
con.commit(); con.close()
os.environ["OT_DB"] = OLD
os.environ["OT_INSTANCE"] = TMP
import app as A
con = sqlite3.connect(OLD)
assert [r[0] for r in con.execute("SELECT name FROM products")] == ["Old Tumbler"]
cols = {r[1] for r in con.execute("PRAGMA table_info(products)")}
assert {"stock", "low_threshold", "low_alert_sent"} <= cols
assert con.execute("SELECT stock, low_threshold FROM products").fetchone() == (0, None)
con.close()
A.init_db()   # running it again is harmless

c = A.app.test_client()
H = {"X-Requested-With": "order-tracker"}
def J(method, url, body=None):
    return getattr(c, method)(url, json=body, headers=H)

os.environ["OT_SMTP_HOST"] = "smtp.example.test"
os.environ["OT_SMTP_USER"] = "me@example.test"
sent = []
A.send_mail = lambda m: sent.append(m)

def stock(pid):
    return next(p["stock"] for p in J("get", "/api/products").get_json() if p["id"] == pid)
def log(pid):
    return J("get", f"/api/products/{pid}/stock-log").get_json()

cust = J("post", "/api/customers", {"name": "Pat"}).get_json()["id"]
shirt = J("post", "/api/products", {"name": "Shirt", "unit_price": "20", "stock": 10}).get_json()["id"]
pen = J("post", "/api/products", {"name": "Pen", "unit_price": "8", "stock": "30"}).get_json()["id"]
assert stock(shirt) == 10 and stock(pen) == 30
assert [(l["change"], l["reason"]) for l in log(shirt)["log"]] == [(10, "Starting stock")]
assert J("post", "/api/products", {"name": "X", "unit_price": "1", "stock": -3}).status_code == 400
assert J("post", "/api/products", {"name": "X", "unit_price": "1", "stock": "2.5"}).status_code == 400
assert J("post", "/api/products", {"name": "X", "unit_price": "1", "stock": "abc"}).status_code == 400
nostock = J("post", "/api/products", {"name": "NoStock", "unit_price": "1"}).get_json()["id"]
assert stock(nostock) == 0 and log(nostock)["log"] == []

def order(items, status="New", **kw):
    return dict({"customer_id": cust, "order_date": "2026-11-15", "status": status, "amount_paid": "0", "items": items}, **kw)
def line(pid, qty, desc=""):
    return {"product_id": pid, "description": desc, "qty": qty, "unit_price": "1"}

# ---------------- stock comes off when the order is ENTERED
r = J("post", "/api/orders", order([line(shirt, 4), line(pen, 10), {"description": "custom", "qty": 5, "unit_price": "2"}]))
o1 = r.get_json()["id"]
assert r.get_json()["warnings"] == []
assert stock(shirt) == 6 and stock(pen) == 20                       # custom item touches nothing
assert log(shirt)["log"][0]["reason"] == f"Order #{o1} entered" and log(shirt)["log"][0]["change"] == -4

# two items of the same product on one order are added together
o2 = J("post", "/api/orders", order([line(pen, 3), line(pen, 2)])).get_json()["id"]
assert stock(pen) == 15

# ---------------- not enough: warns, still saves, stock shows how many are missing
r = J("post", "/api/orders", order([line(shirt, 9)]))
o3 = r.get_json()["id"]
assert r.status_code == 201 and r.get_json()["warnings"] == [{"name": "Shirt", "short": 3}]
assert stock(shirt) == -3

# ---------------- editing moves only the difference
r = J("put", f"/api/orders/{o3}", order([line(shirt, 5)]))
assert r.get_json()["warnings"] == [] and stock(shirt) == 1            # was -3, give back 4
r = J("put", f"/api/orders/{o3}", order([line(shirt, 5), line(pen, 1)]))
assert stock(shirt) == 1 and stock(pen) == 14
r = J("put", f"/api/orders/{o3}", order([line(pen, 1)]))               # shirt line removed entirely
assert stock(shirt) == 6 and stock(pen) == 14
assert [l["reason"] for l in log(shirt)["log"]][0] == f"Order #{o3} edited"
J("put", f"/api/orders/{o3}", order([line(shirt, 5), line(pen, 1)]))   # put it back for the next checks
assert stock(shirt) == 1 and stock(pen) == 14

# ---------------- moving stages does not touch stock, except to/from Cancelled
before = (stock(shirt), stock(pen))
for st in ("Pending", "Ready for Pickup", "Shipped", "Completed"):
    assert J("post", f"/api/orders/{o3}/status", {"status": st}).status_code == 200
assert (stock(shirt), stock(pen)) == before
J("post", f"/api/orders/{o3}/status", {"status": "Cancelled"})
assert stock(shirt) == 6 and stock(pen) == 15                           # cancelled: everything goes back
J("post", f"/api/orders/{o3}/status", {"status": "Cancelled"})          # cancelling twice does nothing more
assert stock(shirt) == 6 and stock(pen) == 15
r = J("post", f"/api/orders/{o3}/status", {"status": "Pending"})        # reinstated: takes it out again
assert (stock(shirt), stock(pen)) == (1, 14) and r.get_json()["warnings"] == []
assert log(shirt)["log"][0]["reason"] == f"Order #{o3} reinstated"
# saving an order as Cancelled (via the edit window) also returns the stock
J("put", f"/api/orders/{o3}", order([line(shirt, 5), line(pen, 1)], status="Cancelled"))
assert (stock(shirt), stock(pen)) == (6, 15)
J("put", f"/api/orders/{o3}", order([line(shirt, 5), line(pen, 1)], status="New"))
assert (stock(shirt), stock(pen)) == (1, 14)

# ---------------- deleting an order puts its stock back
assert J("delete", f"/api/orders/{o3}").status_code == 200
assert (stock(shirt), stock(pen)) == (6, 15)
assert J("delete", f"/api/orders/{o3}").status_code == 404
assert (stock(shirt), stock(pen)) == (6, 15)
# a deleted product no longer affects anything
gone = J("post", "/api/products", {"name": "Gone", "unit_price": "1", "stock": 5}).get_json()["id"]
og = J("post", "/api/orders", order([line(gone, 2)])).get_json()["id"]
J("delete", f"/api/products/{gone}")
assert J("delete", f"/api/orders/{og}").status_code == 200

# ---------------- restock / corrections / editing the number
r = J("post", f"/api/products/{shirt}/stock", {"change": 24, "note": "new box"})
assert r.get_json()["stock"] == 30
assert log(shirt)["log"][0]["reason"] == "Restock: new box" and log(shirt)["log"][0]["after"] == 30
assert J("post", f"/api/products/{shirt}/stock", {"change": -2}).get_json()["stock"] == 28
assert log(shirt)["log"][0]["reason"] == "Correction"
for bad in (0, "x", 1.5, 10 ** 7, None):
    assert J("post", f"/api/products/{shirt}/stock", {"change": bad}).status_code == 400, bad
assert J("post", "/api/products/999/stock", {"change": 1}).status_code == 404
J("put", f"/api/products/{shirt}", {"name": "Shirt", "unit_price": "20", "stock": 25})
assert stock(shirt) == 25 and log(shirt)["log"][0]["change"] == -3
J("put", f"/api/products/{shirt}", {"name": "Shirt (renamed)", "unit_price": "20"})   # no stock sent: unchanged
assert stock(shirt) == 25
J("put", f"/api/products/{shirt}", {"name": "Shirt", "unit_price": "20", "stock": ""})
assert stock(shirt) == 25
assert J("get", "/api/products/999/stock-log").status_code == 404

# ---------------- settings + low stock: alerts show, email once per dip
assert J("get", "/api/settings").get_json()["alert_to"] == "me@example.test"
r = J("put", "/api/settings/thresholds", {"items": [{"id": pen, "low_threshold": 20}, {"id": shirt, "low_threshold": 5}]})
assert r.status_code == 200
by = {p["name"]: p for p in J("get", "/api/settings").get_json()["products"]}
assert by["Pen"]["low_threshold"] == 20 and by["Shirt"]["low_threshold"] == 5 and by["Shirt"]["low"] is False
assert by["Pen"]["low"] is True and by["Pen"]["stock"] == 15                      # already under its limit
assert len(sent) == 1 and "Pen: 15 left (alert at 20)" in sent[0].get_content() and sent[0]["To"] == "me@example.test"
assert [x["name"] for x in J("get", "/api/stats").get_json()["low_stock"]] == ["Pen"]
J("post", "/api/orders", order([line(pen, 1)]))                                   # still low: no second email
assert len(sent) == 1
J("post", f"/api/products/{pen}/stock", {"change": 30})                           # restocked above the limit: re-armed
assert len(sent) == 1 and J("get", "/api/stats").get_json()["low_stock"] == []
oo = J("post", "/api/orders", order([line(pen, 30)])).get_json()["id"]            # drops under again: one new email
assert len(sent) == 2 and "Pen: 14 left" in sent[1].get_content()
# shirt crosses its own, lower limit independently
J("post", "/api/orders", order([line(shirt, 21)]))
assert len(sent) == 3 and "Shirt: 4 left (alert at 5)" in sent[2].get_content()
# clearing a limit removes the alert
J("put", "/api/settings/thresholds", {"items": [{"id": shirt, "low_threshold": ""}]})
assert [x["name"] for x in J("get", "/api/stats").get_json()["low_stock"]] == ["Pen"]
for bad in ([{"id": shirt, "low_threshold": -1}], [{"id": shirt, "low_threshold": "x"}], [{"id": "a", "low_threshold": 1}], "nope"):
    assert J("put", "/api/settings/thresholds", {"items": bad}).status_code == 400
n = len(sent)
J("post", f"/api/orders/{oo}/status", {"status": "Cancelled"})                    # cancelling puts stock back, no email
assert len(sent) == n

# a failed email never breaks the order and is retried at the next stock change
def boom(m): raise smtplib.SMTPAuthenticationError(535, b"bad login")
A.send_mail = boom
J("put", "/api/settings/thresholds", {"items": [{"id": shirt, "low_threshold": 100}]})
r = J("post", "/api/orders", order([line(shirt, 1)]))
assert r.status_code == 201
A.send_mail = lambda m: sent.append(m)
J("post", "/api/orders", order([line(shirt, 1)]))
assert any("Shirt" in m.get_content() for m in sent[n:])

# test email + email off
r = J("post", "/api/settings/test-email", {}); assert r.get_json()["to"] == "me@example.test"
del os.environ["OT_SMTP_HOST"]
assert J("post", "/api/settings/test-email", {}).status_code == 400
assert J("post", "/api/orders", order([line(pen, 1)])).status_code == 201        # orders still work with email off
assert J("get", "/api/settings").get_json()["email_enabled"] is False

print("inventory tests passed")
