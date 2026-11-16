import os, sqlite3, tempfile
TMP = tempfile.mkdtemp()
os.environ["OT_DB"] = os.path.join(TMP, "t.db")
os.environ["OT_INSTANCE"] = TMP

import app as A

c = A.app.test_client()
H = {"X-Requested-With": "order-tracker"}


def J(method, url, body=None):
    return getattr(c, method)(url, json=body, headers=H)


# ---------------- pages and the write guard
assert c.get("/").status_code == 200
assert b"Order Tracker" in c.get("/").data
assert c.post("/api/customers", json={"name": "x"}).status_code == 400            # no header: refused
assert c.post("/api/customers", data={"name": "x"}, headers=H).status_code == 400  # not JSON: refused
cfg = J("get", "/api/config").get_json()
assert cfg["statuses"] == ["New", "Pending", "Ready for Pickup", "Shipped", "Completed", "Cancelled"]
assert cfg["email_enabled"] is False

# ---------------- customers
r = J("post", "/api/customers", {"name": "Brittney Rogas", "phone": "(555) 111-2222", "email": "b@example.com", "address": "1 Main St"})
assert r.status_code == 201
bid = r.get_json()["id"]
assert J("post", "/api/customers", {"name": "brittney rogas"}).status_code == 409        # case-insensitive duplicate
assert J("post", "/api/customers", {"name": ""}).status_code == 400
assert J("post", "/api/customers", {"name": "Bad", "email": "nope"}).status_code == 400
hid = J("post", "/api/customers", {"name": "Holli Nixon"}).get_json()["id"]
assert J("put", f"/api/customers/{hid}", {"name": "Holli Nixon", "phone": "5"}).status_code == 200
assert J("put", "/api/customers/999", {"name": "Z"}).status_code == 404

# ---------------- categories + products
pens = J("post", "/api/categories", {"name": "Pens"}).get_json()["id"]
shirts = J("post", "/api/categories", {"name": "Shirts"}).get_json()["id"]
assert J("post", "/api/categories", {"name": "pens"}).status_code == 409
pen = J("post", "/api/products", {"name": "Pen, refillable", "unit_price": "8", "category_id": pens}).get_json()["id"]
tee = J("post", "/api/products", {"name": "T-Shirt", "unit_price": "$20.00", "category_id": shirts, "description": "Cotton"}).get_json()["id"]
assert J("post", "/api/products", {"name": "Bad", "unit_price": "abc"}).status_code == 400
assert J("post", "/api/products", {"name": "Bad", "unit_price": "-1"}).status_code == 400
assert J("post", "/api/products", {"name": "Bad", "unit_price": "1", "category_id": 999}).status_code == 400
prods = J("get", "/api/products").get_json()
assert {p["name"]: p["unit_price"] for p in prods} == {"Pen, refillable": "8.00", "T-Shirt": "20.00"}

# ---------------- create an order with items (existing customer)
order = {"customer_id": bid, "order_date": "2026-11-09", "status": "Pending", "payment_method": "Cash", "amount_paid": "0",
         "items": [{"product_id": tee, "description": "blue shirt", "qty": 1, "unit_price": "20"},
                   {"product_id": None, "product_name": "", "description": "Custom Pen", "qty": 1, "unit_price": "8"}]}
r = J("post", "/api/orders", order)
assert r.status_code == 201, r.get_json()
oid = r.get_json()["id"]
o = J("get", f"/api/orders/{oid}").get_json()
assert o["total"] == "28.00" and o["balance"] == "28.00" and o["status"] == "Pending" and o["completion_date"] is None
assert [i["product_name"] for i in o["items"]] == ["T-Shirt", "Custom item"]
assert o["items"][0]["line_total"] == "20.00"

# validation
bad = dict(order, items=[])
assert J("post", "/api/orders", bad).status_code == 400
assert J("post", "/api/orders", dict(order, status="Nope")).status_code == 400
assert J("post", "/api/orders", dict(order, order_date="11/09/2026")).status_code == 400
assert J("post", "/api/orders", dict(order, customer_id=999)).status_code == 400
assert J("post", "/api/orders", {k: v for k, v in order.items() if k != "customer_id"}).status_code == 400
assert J("post", "/api/orders", dict(order, items=[{"qty": 0, "unit_price": "1", "description": "x"}])).status_code == 400
assert J("post", "/api/orders", dict(order, items=[{"qty": 1, "unit_price": "1"}])).status_code == 400   # no product and no description
assert len(J("get", "/api/orders").get_json()) == 1                                                        # nothing half-saved

# ---------------- create an order with a NEW customer in one step
r = J("post", "/api/orders", dict(order, status="New", customer_id=None, new_customer={"name": "Melissa Clairborne", "email": "m@example.com"},
                                 items=[{"product_id": pen, "qty": 3, "unit_price": "8.50"}], amount_paid="10"))
assert r.status_code == 201, r.get_json()
oid2 = r.get_json()["id"]
o2 = J("get", f"/api/orders/{oid2}").get_json()
assert o2["total"] == "25.50" and o2["amount_paid"] == "10.00" and o2["balance"] == "15.50" and o2["customer_name"] == "Melissa Clairborne"
assert J("post", "/api/orders", dict(order, customer_id=None, new_customer={"name": "Melissa Clairborne"})).status_code == 409
assert len(J("get", "/api/customers").get_json()) == 3

# ---------------- edit: add more items later, change fields
upd = dict(order, items=o["items"] and [
    {"product_id": tee, "product_name": "T-Shirt", "description": "blue shirt", "qty": 2, "unit_price": "20"},
    {"product_id": None, "product_name": "Custom item", "description": "Custom Pen", "qty": 1, "unit_price": "8"},
    {"product_id": pen, "description": "", "qty": 1, "unit_price": "8"}], amount_paid="20")
assert J("put", f"/api/orders/{oid}", upd).status_code == 200
o = J("get", f"/api/orders/{oid}").get_json()
assert o["total"] == "56.00" and o["balance"] == "36.00" and len(o["items"]) == 3
assert J("put", "/api/orders/999", upd).status_code == 404

# ---------------- price history survives product edits and deletes
J("put", f"/api/products/{tee}", {"name": "T-Shirt", "unit_price": "99", "category_id": shirts})
assert J("get", f"/api/orders/{oid}").get_json()["total"] == "56.00"
J("delete", f"/api/products/{pen}")
o2 = J("get", f"/api/orders/{oid2}").get_json()
assert o2["total"] == "25.50" and o2["items"][0]["product_name"] == "Pen, refillable" and o2["items"][0]["product_id"] is None

# ---------------- status changes + completion date
assert J("post", f"/api/orders/{oid}/status", {"status": "Completed", "completion_date": "2026-11-12"}).status_code == 200
assert J("get", f"/api/orders/{oid}").get_json()["completion_date"] == "2026-11-12"
J("post", f"/api/orders/{oid}/status", {"status": "Completed", "completion_date": "2026-12-01"})
assert J("get", f"/api/orders/{oid}").get_json()["completion_date"] == "2026-11-12"   # keeps the first completion date
J("post", f"/api/orders/{oid}/status", {"status": "Shipped"})
assert J("get", f"/api/orders/{oid}").get_json()["completion_date"] is None
assert J("post", f"/api/orders/{oid}/status", {"status": "Bogus"}).status_code == 400
assert J("post", "/api/orders/999/status", {"status": "New"}).status_code == 404
J("post", f"/api/orders/{oid}/status", {"status": "Pending"})

# ---------------- filters
def ids(q=""):
    return sorted(x["id"] for x in J("get", "/api/orders" + q).get_json())
assert ids() == [oid, oid2]
assert ids(f"?order_no={oid}") == [oid] and ids(f"?order_no=%23{oid2}") == [oid2]
assert J("get", "/api/orders?order_no=abc").status_code == 400
assert ids(f"?customer_id={bid}") == [oid]
assert ids("?status=Pending") == [oid] and ids("?status=New") == [oid2]
assert ids("?status=Open") == [oid, oid2]
assert ids("?start=2026-11-09&end=2026-11-09") == [oid, oid2]
assert ids("?start=2030-01-01") == []
assert J("get", "/api/orders?status=Bogus").status_code == 400
assert J("get", "/api/orders?start=junk").status_code == 400

# ---------------- stats
s = J("get", "/api/stats").get_json()
by = {x["status"]: x for x in s["by_status"]}
assert by["Pending"]["count"] == 1 and by["New"]["count"] == 1 and by["Completed"]["count"] == 0
assert by["Pending"]["balance"] == "36.00" and by["New"]["balance"] == "15.50"
assert s["outstanding"] == "51.50"
cs = {x["name"]: x for x in s["customers"]}
assert cs["Brittney Rogas"]["orders"] == 1 and cs["Brittney Rogas"]["open"] == 1 and cs["Holli Nixon"]["orders"] == 0
J("post", f"/api/orders/{oid2}/status", {"status": "Cancelled"})
s = J("get", "/api/stats").get_json()
assert s["outstanding"] == "36.00"                                  # cancelled orders are not owed
assert {x["name"]: x for x in s["customers"]}["Melissa Clairborne"]["ordered"] == "0.00"

# ---------------- quote page
q = c.get(f"/orders/{oid}/quote")
assert q.status_code == 200 and b"blue shirt" in q.data and b"$56.00" in q.data and b"Balance due" in q.data
assert c.get("/orders/999/quote").status_code == 404
J("post", f"/api/orders/{oid}/status", {"status": "New"})
xss = J("post", "/api/orders", dict(order, items=[{"description": "<script>alert(1)</script>", "qty": 1, "unit_price": "1"}])).get_json()["id"]
assert b"<script>alert(1)</script>" not in c.get(f"/orders/{xss}/quote").data

# ---------------- email
r = J("post", f"/api/orders/{oid}/email", {})
assert r.status_code == 400 and "not set up" in r.get_json()["error"]
os.environ["OT_SMTP_HOST"] = "smtp.example.test"
os.environ["OT_SMTP_FROM"] = "shop@example.test"
assert J("get", "/api/config").get_json()["email_enabled"] is True
sent = []
A.send_mail = lambda m: sent.append(m)
J("put", f"/api/customers/{bid}", {"name": "Brittney Rogas", "email": ""})
r = J("post", f"/api/orders/{oid}/email", {"message": "Thanks!"})
assert r.status_code == 400 and "no email" in r.get_json()["error"]                       # no address on file and none typed
J("put", f"/api/customers/{bid}", {"name": "Brittney Rogas", "email": "b@example.com"})
r = J("post", f"/api/orders/{oid}/email", {"message": "Thanks!"})
assert r.status_code == 200 and r.get_json()["to"] == "b@example.com", r.get_json()
m = sent[0]
assert m["To"] == "b@example.com" and m["From"] == "shop@example.test" and f"Order #{oid}" in m["Subject"]
assert "Thanks!" in m.get_body(("plain",)).get_content() and "$56.00" in m.get_body(("html",)).get_content()
assert J("post", f"/api/orders/{oid}/email", {"to": "not an email"}).status_code == 400
assert J("post", "/api/orders/999/email", {}).status_code == 404
J("post", f"/api/orders/{oid}/email", {"to": "other@example.com"})
assert sent[-1]["To"] == "other@example.com"
import smtplib
def boom(m): raise smtplib.SMTPAuthenticationError(535, b"bad login")
A.send_mail = boom
r = J("post", f"/api/orders/{oid}/email", {})
assert r.status_code == 502 and "could not be sent" in r.get_json()["error"]

# ---------------- deletes
assert J("delete", f"/api/customers/{bid}").status_code == 409        # has orders
assert J("delete", f"/api/orders/{oid}").status_code == 200
assert J("get", f"/api/orders/{oid}").status_code == 404
assert J("delete", f"/api/customers/{bid}").status_code == 409        # still has the XSS-check order
assert J("delete", f"/api/orders/{xss}").status_code == 200
assert J("delete", f"/api/customers/{bid}").status_code == 200
assert J("delete", "/api/orders/999").status_code == 404
J("delete", f"/api/categories/{shirts}")
assert [p["category"] for p in J("get", "/api/products").get_json()] == [""]
assert sqlite3.connect(os.environ["OT_DB"]).execute("SELECT COUNT(*) FROM order_items WHERE order_id=?", (oid,)).fetchone()[0] == 0

print("all tests passed")
