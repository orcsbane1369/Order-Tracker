"""Order Tracker - Flask + SQLite. Run: python app.py  ->  http://127.0.0.1:5000

Home-network tool: there is no login. Do not expose it to the internet.
"""
import os
import re
import smtplib
import sqlite3
import ssl
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP, InvalidOperation
from email.message import EmailMessage

from flask import Flask, abort, g, jsonify, render_template, request

BASE = os.path.dirname(os.path.abspath(__file__))
INSTANCE = os.environ.get("OT_INSTANCE") or os.path.join(BASE, "instance")   # database lives here
os.makedirs(INSTANCE, exist_ok=True)
DB_PATH = os.environ.get("OT_DB") or os.path.join(INSTANCE, "orders.db")

BUSINESS_NAME = os.environ.get("OT_BUSINESS_NAME", "Our Creations")
BUSINESS_CONTACT = os.environ.get("OT_BUSINESS_CONTACT", "")     # e.g. phone / email shown on quotes
QUOTE_NOTE = os.environ.get("OT_QUOTE_NOTE", "")                 # e.g. "Quote valid for 30 days."

STATUSES = ["New", "Pending", "Ready for Pickup", "Shipped", "Completed", "Cancelled"]
PAYMENT_METHODS = ["Cash", "Check", "Card", "Venmo", "Zelle", "PayPal", "Cash App", "Other"]
OPEN_STATUSES = ["New", "Pending", "Ready for Pickup", "Shipped"]

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=1024 * 1024)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  unit_price_c INTEGER NOT NULL DEFAULT 0,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  stock INTEGER NOT NULL DEFAULT 0,                 -- units on hand (orders that are not cancelled have already taken theirs)
  low_threshold INTEGER,                            -- alert when stock is at or below this; empty = no alert for this product
  low_alert_sent INTEGER NOT NULL DEFAULT 0);       -- 1 once the alert email went out; reset when stock rises above the threshold
CREATE TABLE IF NOT EXISTS stock_log(
  id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  change INTEGER NOT NULL, stock_after INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',
  order_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_stocklog_product ON stock_log(product_id, id);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
  order_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'New',
  payment_method TEXT NOT NULL DEFAULT '',
  amount_paid_c INTEGER NOT NULL DEFAULT 0,
  completion_date TEXT,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS order_items(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
  product_name TEXT NOT NULL DEFAULT '',   -- copied at order time so later product edits do not change old orders
  description TEXT NOT NULL DEFAULT '',    -- customization notes
  qty INTEGER NOT NULL DEFAULT 1, unit_price_c INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_items_order ON order_items(order_id);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(SCHEMA)
        # Upgrade a database made before inventory existed: add the new product columns.
        have = {r[1] for r in c.execute("PRAGMA table_info(products)")}
        for col, ddl in (("stock", "INTEGER NOT NULL DEFAULT 0"), ("low_threshold", "INTEGER"),
                         ("low_alert_sent", "INTEGER NOT NULL DEFAULT 0")):
            if col not in have:
                c.execute(f"ALTER TABLE products ADD COLUMN {col} {ddl}")


init_db()


class Bad(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


@app.errorhandler(Bad)
def _bad(e):
    return jsonify(error=e.msg), e.code


@app.before_request
def require_json_writes():
    # A browser on another site cannot send JSON without a permission check, so this blocks drive-by form posts.
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.path.startswith("/api/"):
        if request.method != "DELETE" and not request.is_json:
            raise Bad("Send JSON")
        if request.headers.get("X-Requested-With") != "order-tracker":
            raise Bad("Bad request header")


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
        "form-action 'self'")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- helpers

def cents(v, what="Amount"):
    try:
        d = D(str(v).replace("$", "").replace(",", "").strip() or "0")
    except InvalidOperation:
        raise Bad(f"{what} is not a number")
    if not d.is_finite() or d < 0 or d > D("9999999"):
        raise Bad(f"{what} must be between 0 and 9,999,999")
    return int((d * 100).quantize(D("1"), rounding=ROUND_HALF_UP))


def money(c):
    return "${:,.2f}".format((c or 0) / 100)


def dollars(c):
    return "{:.2f}".format((c or 0) / 100)


def iso_date(v, what="Date", required=True):
    v = (v or "").strip() if isinstance(v, str) or v is None else str(v)
    if not v:
        if required:
            raise Bad(f"{what} is required")
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise Bad(f"{what} is not a valid date")


def text(v, what, maxlen, required=False):
    v = (v or "").strip() if isinstance(v, str) or v is None else str(v).strip()
    if required and not v:
        raise Bad(f"{what} is required")
    if len(v) > maxlen:
        raise Bad(f"{what} is too long (max {maxlen} characters)")
    return v


EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise Bad("Send a JSON object")
    return data



# ---------------------------------------------------------------- pages

@app.get("/")
def index():
    return render_template("index.html", business=BUSINESS_NAME)


# ---------------------------------------------------------------- config / lists

def email_ready():
    return bool(os.environ.get("OT_SMTP_HOST"))


@app.get("/api/config")
def config():
    return jsonify(statuses=STATUSES, payment_methods=PAYMENT_METHODS, open_statuses=OPEN_STATUSES,
                   email_enabled=email_ready(), business=BUSINESS_NAME)


# ---------------------------------------------------------------- customers

def cust_json(r):
    return {"id": r["id"], "name": r["name"], "phone": r["phone"], "email": r["email"], "address": r["address"]}


def cust_fields(d):
    name = text(d.get("name"), "Name", 80, required=True)
    phone = text(d.get("phone"), "Contact number", 40)
    email = text(d.get("email"), "Email", 120)
    if email and not EMAIL_RE.match(email):
        raise Bad("That email address does not look right")
    address = text(d.get("address"), "Address", 300)
    return name, phone, email, address


@app.get("/api/customers")
def customers_list():
    rows = db().execute(
        "SELECT c.*, (SELECT COUNT(*) FROM orders o WHERE o.customer_id=c.id) AS n FROM customers c ORDER BY c.name").fetchall()
    return jsonify([dict(cust_json(r), orders=r["n"]) for r in rows])


@app.post("/api/customers")
def customers_add():
    name, phone, email, address = cust_fields(body())
    try:
        cur = db().execute("INSERT INTO customers(name,phone,email,address) VALUES(?,?,?,?)", (name, phone, email, address))
    except sqlite3.IntegrityError:
        raise Bad("A customer with that name already exists", 409)
    db().commit()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/customers/<int:cid>")
def customers_update(cid):
    name, phone, email, address = cust_fields(body())
    try:
        cur = db().execute("UPDATE customers SET name=?,phone=?,email=?,address=? WHERE id=?", (name, phone, email, address, cid))
    except sqlite3.IntegrityError:
        raise Bad("A customer with that name already exists", 409)
    if not cur.rowcount:
        raise Bad("Customer not found", 404)
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/customers/<int:cid>")
def customers_delete(cid):
    n = db().execute("SELECT COUNT(*) FROM orders WHERE customer_id=?", (cid,)).fetchone()[0]
    if n:
        raise Bad(f"This customer has {n} order(s). Delete or reassign those orders first.", 409)
    cur = db().execute("DELETE FROM customers WHERE id=?", (cid,))
    if not cur.rowcount:
        raise Bad("Customer not found", 404)
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- categories + products

@app.get("/api/categories")
def categories_list():
    rows = db().execute(
        "SELECT c.*, (SELECT COUNT(*) FROM products p WHERE p.category_id=c.id) AS n FROM categories c ORDER BY c.name").fetchall()
    return jsonify([{"id": r["id"], "name": r["name"], "products": r["n"]} for r in rows])


@app.post("/api/categories")
def categories_add():
    name = text(body().get("name"), "Category name", 60, required=True)
    try:
        cur = db().execute("INSERT INTO categories(name) VALUES(?)", (name,))
    except sqlite3.IntegrityError:
        raise Bad("That category already exists", 409)
    db().commit()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/categories/<int:cid>")
def categories_update(cid):
    name = text(body().get("name"), "Category name", 60, required=True)
    try:
        cur = db().execute("UPDATE categories SET name=? WHERE id=?", (name, cid))
    except sqlite3.IntegrityError:
        raise Bad("That category already exists", 409)
    if not cur.rowcount:
        raise Bad("Category not found", 404)
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/categories/<int:cid>")
def categories_delete(cid):
    cur = db().execute("DELETE FROM categories WHERE id=?", (cid,))   # its products become "no category"
    if not cur.rowcount:
        raise Bad("Category not found", 404)
    db().commit()
    return jsonify(ok=True)


def is_low(r):
    return r["low_threshold"] is not None and r["stock"] <= r["low_threshold"]


def prod_json(r):
    return {"id": r["id"], "name": r["name"], "description": r["description"], "unit_price": dollars(r["unit_price_c"]),
            "category_id": r["category_id"], "category": r["category"] or "", "stock": r["stock"],
            "low_threshold": r["low_threshold"], "low": is_low(r)}


@app.get("/api/products")
def products_list():
    rows = db().execute(
        "SELECT p.*, c.name AS category FROM products p LEFT JOIN categories c ON c.id=p.category_id ORDER BY p.name").fetchall()
    return jsonify([prod_json(r) for r in rows])


def whole(v, what, lo=-1000000, hi=1000000, blank_ok=False):
    if v is None or (isinstance(v, str) and not v.strip()):
        if blank_ok:
            return None
        raise Bad(f"{what} is required")
    try:
        d = D(str(v).strip())
    except InvalidOperation:
        raise Bad(f"{what} must be a whole number")
    if d != d.to_integral_value() or not (lo <= d <= hi):
        raise Bad(f"{what} must be a whole number between {lo:,} and {hi:,}")
    return int(d)


def prod_fields(d):
    name = text(d.get("name"), "Product name", 80, required=True)
    desc = text(d.get("description"), "Description", 500)
    price = cents(d.get("unit_price"), "Unit price")
    cat = d.get("category_id")
    if cat in ("", None):
        cat = None
    else:
        try:
            cat = int(cat)
        except (TypeError, ValueError):
            raise Bad("Bad category")
        if not db().execute("SELECT 1 FROM categories WHERE id=?", (cat,)).fetchone():
            raise Bad("Category not found")
    return name, desc, price, cat


# ---- inventory

def adjust_stock(pid, change, reason, order_id=None):
    """Move a product's stock and write the log line. Caller commits."""
    if not change:
        return
    c = db()
    c.execute("UPDATE products SET stock=stock+? WHERE id=?", (change, pid))
    after = c.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()["stock"]
    c.execute("INSERT INTO stock_log(product_id,change,stock_after,reason,order_id) VALUES(?,?,?,?,?)",
              (pid, change, after, reason, order_id))


def alert_to():
    return os.environ.get("OT_SMTP_USER") or os.environ.get("OT_SMTP_FROM") or ""


def low_check(pids):
    """Work out which products have just reached their limit (alert due) and re-arm the ones that recovered."""
    c, due = db(), []
    for pid in set(pids):
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not r:
            continue
        if is_low(r) and not r["low_alert_sent"]:
            due.append(r)
        elif not is_low(r) and r["low_alert_sent"]:
            c.execute("UPDATE products SET low_alert_sent=0 WHERE id=?", (pid,))
    c.commit()
    return due


def send_low_alerts(rows):
    """Email one message listing the products that reached their limit. Returns an error text, or '' when sent."""
    if not rows:
        return ""
    if not email_ready() or not alert_to():
        return "Email is not set up"
    lines = [f"{r['name']}: {r['stock']} left (alert at {r['low_threshold']})" for r in rows]
    msg = EmailMessage()
    msg["Subject"] = f"Low stock: {', '.join(r['name'] for r in rows)[:120]}"
    msg["From"] = os.environ.get("OT_SMTP_FROM") or os.environ.get("OT_SMTP_USER") or "orders@localhost"
    msg["To"] = alert_to()
    msg.set_content(f"{BUSINESS_NAME} - these products are at or below their low-stock limit:\n\n" + "\n".join(lines)
                    + "\n\nYou will not get another email for a product until it has been restocked above its limit.")
    try:
        send_mail(msg)
    except (smtplib.SMTPException, OSError) as e:
        return str(e)
    db().executemany("UPDATE products SET low_alert_sent=1 WHERE id=?", [(r["id"],) for r in rows])
    db().commit()
    return ""


def after_stock_change(pids):
    """Call after the transaction is committed: sends any low-stock email that is now due."""
    send_low_alerts(low_check(pids))


def shortages(pids):
    """Products (from this order) whose stock is below zero: what you still need to find."""
    out = []
    for pid in set(pids):
        r = db().execute("SELECT name, stock FROM products WHERE id=?", (pid,)).fetchone()
        if r and r["stock"] < 0:
            out.append({"name": r["name"], "short": -r["stock"]})
    return sorted(out, key=lambda x: x["name"].lower())


def reserved(oid):
    """What an order has taken out of stock right now: {product_id: qty}. Cancelled orders hold nothing."""
    row = db().execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()
    if not row or row["status"] == "Cancelled":
        return {}
    return {r["product_id"]: r["n"] for r in db().execute(
        "SELECT product_id, SUM(qty) AS n FROM order_items WHERE order_id=? AND product_id IS NOT NULL GROUP BY product_id", (oid,))}


def sync_stock(oid, before, reason):
    """Compare what the order held before with what it holds now and move stock by the difference."""
    after = reserved(oid)
    changed = []
    for pid in set(before) | set(after):
        d = after.get(pid, 0) - before.get(pid, 0)
        if d:
            adjust_stock(pid, -d, f"Order #{oid} {reason}", oid)
            changed.append(pid)
    return after, changed


@app.post("/api/products")
def products_add():
    d = body()
    f = prod_fields(d)
    stock = whole(d.get("stock"), "Current inventory", lo=0, blank_ok=True) or 0
    cur = db().execute("INSERT INTO products(name,description,unit_price_c,category_id) VALUES(?,?,?,?)", f)
    adjust_stock(cur.lastrowid, stock, "Starting stock")
    db().commit()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/products/<int:pid>")
def products_update(pid):
    d = body()
    f = prod_fields(d)
    old = db().execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()
    if not old:
        raise Bad("Product not found", 404)
    new = whole(d.get("stock"), "Current inventory", blank_ok=True)
    db().execute("UPDATE products SET name=?,description=?,unit_price_c=?,category_id=? WHERE id=?", (*f, pid))
    if new is not None and new != old["stock"]:
        adjust_stock(pid, new - old["stock"], "Correction (edited the number)")
    db().commit()
    after_stock_change([pid])
    return jsonify(ok=True)


@app.delete("/api/products/<int:pid>")
def products_delete(pid):
    cur = db().execute("DELETE FROM products WHERE id=?", (pid,))   # old order lines keep their copied name and price
    if not cur.rowcount:
        raise Bad("Product not found", 404)
    db().commit()
    return jsonify(ok=True)


@app.post("/api/products/<int:pid>/stock")
def products_add_stock(pid):
    d = body()
    n = whole(d.get("change"), "Quantity", lo=-100000, hi=100000)
    if n == 0:
        raise Bad("Enter a quantity other than zero")
    if not db().execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
        raise Bad("Product not found", 404)
    note = text(d.get("note"), "Note", 200)
    reason = ("Restock" if n > 0 else "Correction") + (": " + note if note else "")
    adjust_stock(pid, n, reason)
    db().commit()
    after_stock_change([pid])
    return jsonify(stock=db().execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()["stock"])


@app.get("/api/products/<int:pid>/stock-log")
def products_stock_log(pid):
    p = db().execute("SELECT name, stock FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        raise Bad("Product not found", 404)
    rows = db().execute("SELECT * FROM stock_log WHERE product_id=? ORDER BY id DESC LIMIT 300", (pid,)).fetchall()
    return jsonify(name=p["name"], stock=p["stock"], log=[
        {"when": r["created_at"], "change": r["change"], "after": r["stock_after"], "reason": r["reason"], "order_id": r["order_id"]} for r in rows])


# ---- settings (low-stock limits)

@app.get("/api/settings")
def settings_get():
    rows = db().execute("SELECT p.*, c.name AS category FROM products p LEFT JOIN categories c ON c.id=p.category_id ORDER BY p.name").fetchall()
    return jsonify(alert_to=alert_to() if email_ready() else "", email_enabled=email_ready(), products=[prod_json(r) for r in rows])


@app.put("/api/settings/thresholds")
def settings_thresholds():
    items = body().get("items")
    if not isinstance(items, list):
        raise Bad("Send a list of items")
    ids = []
    for it in items:
        if not isinstance(it, dict) or not str(it.get("id", "")).isdigit():
            raise Bad("Bad item")
        t = whole(it.get("low_threshold"), "Alert level", lo=0, hi=1000000, blank_ok=True)
        cur = db().execute("UPDATE products SET low_threshold=? WHERE id=?", (t, int(it["id"])))
        if cur.rowcount:
            ids.append(int(it["id"]))
    db().commit()
    after_stock_change(ids)
    return jsonify(ok=True)


@app.post("/api/settings/test-email")
def settings_test_email():
    if not email_ready() or not alert_to():
        raise Bad("Email is not set up yet. Add the OT_SMTP_* settings (see README).")
    msg = EmailMessage()
    msg["Subject"] = f"Test from {BUSINESS_NAME} Order Tracker"
    msg["From"] = os.environ.get("OT_SMTP_FROM") or os.environ.get("OT_SMTP_USER") or "orders@localhost"
    msg["To"] = alert_to()
    msg.set_content("Low-stock alerts will arrive at this address.")
    try:
        send_mail(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise Bad(f"The email could not be sent: {e}", 502)
    return jsonify(ok=True, to=alert_to())


# ---------------------------------------------------------------- orders

ORDER_SELECT = """
SELECT o.*, c.name AS customer_name, c.email AS customer_email,
  COALESCE((SELECT SUM(i.qty*i.unit_price_c) FROM order_items i WHERE i.order_id=o.id),0) AS total_c,
  (SELECT COUNT(*) FROM order_items i WHERE i.order_id=o.id) AS item_count
FROM orders o JOIN customers c ON c.id=o.customer_id
"""


def order_json(r):
    total = r["total_c"]
    return {"id": r["id"], "customer_id": r["customer_id"], "customer_name": r["customer_name"],
            "customer_email": r["customer_email"], "order_date": r["order_date"], "status": r["status"],
            "payment_method": r["payment_method"], "amount_paid": dollars(r["amount_paid_c"]),
            "total": dollars(total), "balance": dollars(total - r["amount_paid_c"]),
            "completion_date": r["completion_date"], "notes": r["notes"], "item_count": r["item_count"]}


def item_json(r):
    return {"id": r["id"], "product_id": r["product_id"], "product_name": r["product_name"],
            "description": r["description"], "qty": r["qty"], "unit_price": dollars(r["unit_price_c"]),
            "line_total": dollars(r["qty"] * r["unit_price_c"])}


def get_order(oid):
    r = db().execute(ORDER_SELECT + " WHERE o.id=?", (oid,)).fetchone()
    if not r:
        raise Bad("Order not found", 404)
    items = db().execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,)).fetchall()
    return r, items


@app.get("/api/orders")
def orders_list():
    where, args = [], []
    q = request.args
    if q.get("order_no", "").strip():
        s = q["order_no"].strip().lstrip("#")
        if not s.isdigit():
            raise Bad("Order # must be a number")
        where.append("o.id=?"); args.append(int(s))
    if q.get("customer_id", "").strip():
        where.append("o.customer_id=?"); args.append(int(q["customer_id"]) if q["customer_id"].isdigit() else -1)
    if q.get("status", "").strip():
        if q["status"] == "Open":
            where.append("o.status IN (%s)" % ",".join("?" * len(OPEN_STATUSES))); args += OPEN_STATUSES
        elif q["status"] in STATUSES:
            where.append("o.status=?"); args.append(q["status"])
        else:
            raise Bad("Unknown status")
    if q.get("start", "").strip():
        where.append("o.order_date>=?"); args.append(iso_date(q["start"], "Start date"))
    if q.get("end", "").strip():
        where.append("o.order_date<=?"); args.append(iso_date(q["end"], "End date"))
    sql = ORDER_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY o.id DESC"
    return jsonify([order_json(r) for r in db().execute(sql, args).fetchall()])


@app.get("/api/orders/<int:oid>")
def orders_get(oid):
    r, items = get_order(oid)
    return jsonify(dict(order_json(r), items=[item_json(i) for i in items]))


def clean_items(raw):
    if not isinstance(raw, list) or not raw:
        raise Bad("Add at least one item to the order")
    if len(raw) > 200:
        raise Bad("Too many items on one order")
    out = []
    for n, it in enumerate(raw, 1):
        if not isinstance(it, dict):
            raise Bad("Bad item")
        try:
            qty = int(it.get("qty"))
        except (TypeError, ValueError):
            raise Bad(f"Item {n}: quantity must be a whole number")
        if qty < 1 or qty > 100000:
            raise Bad(f"Item {n}: quantity must be at least 1")
        pid = it.get("product_id")
        pname = text(it.get("product_name"), f"Item {n} name", 120)
        if pid not in (None, ""):
            row = db().execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone() if str(pid).isdigit() else None
            if row:
                pid = int(pid)
                pname = pname or row["name"]
            else:
                pid = None     # product was deleted meanwhile; keep the line as a custom item
        else:
            pid = None
        desc = text(it.get("description"), f"Item {n} customization", 1000)
        if not pname and not desc:
            raise Bad(f"Item {n}: pick a product or describe the custom item")
        out.append((pid, pname or "Custom item", desc, qty, cents(it.get("unit_price"), f"Item {n} price")))
    return out


def save_order(oid, d):
    c = db()
    cust = d.get("customer_id")
    if cust not in (None, ""):
        if not str(cust).isdigit() or not c.execute("SELECT 1 FROM customers WHERE id=?", (cust,)).fetchone():
            raise Bad("Customer not found")
        cust = int(cust)
    else:
        nc = d.get("new_customer")
        if not isinstance(nc, dict):
            raise Bad("Pick a customer or enter a new one")
        name, phone, email, address = cust_fields(nc)
        try:
            cust = c.execute("INSERT INTO customers(name,phone,email,address) VALUES(?,?,?,?)",
                             (name, phone, email, address)).lastrowid
        except sqlite3.IntegrityError:
            raise Bad("A customer with that name already exists. Pick them from the list.", 409)
    status = d.get("status") or "New"
    if status not in STATUSES:
        raise Bad("Unknown status")
    odate = iso_date(d.get("order_date"), "Order date")
    method = text(d.get("payment_method"), "Payment method", 40)
    paid = cents(d.get("amount_paid"), "Amount paid")
    notes = text(d.get("notes"), "Notes", 2000)
    items = clean_items(d.get("items"))
    done = iso_date(d.get("completion_date"), "Completion date", required=False)
    if status == "Completed":
        if oid:
            prev = c.execute("SELECT completion_date FROM orders WHERE id=?", (oid,)).fetchone()
            done = done or (prev["completion_date"] if prev else None)
        done = done or date.today().isoformat()
    else:
        done = None
    if oid is None:
        before, reason = {}, "entered"
        oid = c.execute("INSERT INTO orders(customer_id,order_date,status,payment_method,amount_paid_c,completion_date,notes)"
                        " VALUES(?,?,?,?,?,?,?)", (cust, odate, status, method, paid, done, notes)).lastrowid
    else:
        before = reserved(oid)
        reason = "cancelled" if status == "Cancelled" else "edited"
        cur = c.execute("UPDATE orders SET customer_id=?,order_date=?,status=?,payment_method=?,amount_paid_c=?,"
                        "completion_date=?,notes=? WHERE id=?", (cust, odate, status, method, paid, done, notes, oid))
        if not cur.rowcount:
            raise Bad("Order not found", 404)
        c.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
    c.executemany("INSERT INTO order_items(order_id,product_id,product_name,description,qty,unit_price_c) VALUES(?,?,?,?,?,?)",
                  [(oid, *it) for it in items])
    after, changed = sync_stock(oid, before, reason)      # same transaction: stock and order are saved together
    c.commit()
    after_stock_change(changed)
    return oid, shortages(after)


@app.post("/api/orders")
def orders_add():
    oid, short = save_order(None, body())
    return jsonify(id=oid, warnings=short), 201


@app.put("/api/orders/<int:oid>")
def orders_update(oid):
    _, short = save_order(oid, body())
    return jsonify(ok=True, warnings=short)


@app.post("/api/orders/<int:oid>/status")
def orders_status(oid):
    d = body()
    status = d.get("status")
    if status not in STATUSES:
        raise Bad("Unknown status")
    done = iso_date(d.get("completion_date"), "Completion date", required=False) or date.today().isoformat()
    before = reserved(oid)
    cur = db().execute("UPDATE orders SET status=?, completion_date=CASE WHEN ?='Completed' THEN COALESCE(completion_date,?) ELSE NULL END WHERE id=?",
                       (status, status, done, oid))
    if not cur.rowcount:
        raise Bad("Order not found", 404)
    after, changed = sync_stock(oid, before, "cancelled" if status == "Cancelled" else "reinstated")
    db().commit()
    after_stock_change(changed)
    return jsonify(ok=True, warnings=shortages(after))


@app.delete("/api/orders/<int:oid>")
def orders_delete(oid):
    before = reserved(oid)
    cur = db().execute("DELETE FROM orders WHERE id=?", (oid,))
    if not cur.rowcount:
        raise Bad("Order not found", 404)
    _, changed = sync_stock(oid, before, "deleted")      # the order is gone, so everything it held goes back
    db().commit()
    after_stock_change(changed)
    return jsonify(ok=True)


# ---------------------------------------------------------------- stats

@app.get("/api/stats")
def stats():
    base = """WITH t AS (SELECT o.id, o.status, o.customer_id, o.amount_paid_c,
      COALESCE((SELECT SUM(i.qty*i.unit_price_c) FROM order_items i WHERE i.order_id=o.id),0) AS total_c FROM orders o)"""
    by = {s: {"status": s, "count": 0, "total": "0.00", "balance": "0.00"} for s in STATUSES}
    for r in db().execute(base + " SELECT status, COUNT(*) n, SUM(total_c) tot, SUM(total_c-amount_paid_c) bal FROM t GROUP BY status"):
        by[r["status"]] = {"status": r["status"], "count": r["n"], "total": dollars(r["tot"]), "balance": dollars(r["bal"])}
    ph = ",".join("?" * len(OPEN_STATUSES))
    cust = []
    for r in db().execute(base + f""" SELECT c.id, c.name, COUNT(t.id) n,
        SUM(CASE WHEN t.status IN ({ph}) THEN 1 ELSE 0 END) open_n,
        SUM(CASE WHEN t.status='Completed' THEN 1 ELSE 0 END) done_n,
        COALESCE(SUM(CASE WHEN t.status!='Cancelled' THEN t.total_c END),0) ordered,
        COALESCE(SUM(CASE WHEN t.status!='Cancelled' THEN t.amount_paid_c END),0) paid
      FROM customers c LEFT JOIN t ON t.customer_id=c.id GROUP BY c.id ORDER BY c.name""", OPEN_STATUSES):
        cust.append({"id": r["id"], "name": r["name"], "orders": r["n"], "open": r["open_n"] or 0, "completed": r["done_n"] or 0,
                     "ordered": dollars(r["ordered"]), "paid": dollars(r["paid"]), "balance": dollars(r["ordered"] - r["paid"])})
    live = [r for s, r in by.items() if s != "Cancelled"]
    owed = sum(D(r["balance"]) for r in live)
    low = [{"id": r["id"], "name": r["name"], "stock": r["stock"], "threshold": r["low_threshold"]} for r in db().execute(
        "SELECT id,name,stock,low_threshold FROM products WHERE low_threshold IS NOT NULL AND stock<=low_threshold ORDER BY stock, name")]
    return jsonify(by_status=[by[s] for s in STATUSES], customers=cust, outstanding=str(owed), low_stock=low)


# ---------------------------------------------------------------- quotes (print page + email)

def quote_data(oid):
    r, items = get_order(oid)
    cust = db().execute("SELECT * FROM customers WHERE id=?", (r["customer_id"],)).fetchone()
    rows = [{"name": i["product_name"], "description": i["description"], "qty": i["qty"],
             "unit": money(i["unit_price_c"]), "total": money(i["qty"] * i["unit_price_c"])} for i in items]
    return {"order": r, "customer": cust, "items": rows, "total": money(r["total_c"]), "paid": money(r["amount_paid_c"]),
            "balance": money(r["total_c"] - r["amount_paid_c"]), "business": BUSINESS_NAME, "contact": BUSINESS_CONTACT,
            "note": QUOTE_NOTE}


@app.get("/orders/<int:oid>/quote")
def quote_page(oid):
    try:
        q = quote_data(oid)
    except Bad:
        abort(404)
    return render_template("quote.html", **q)


def quote_text(q, message=""):
    lines = []
    if message:
        lines += [message, ""]
    lines += [f"{q['business']} - Quote for Order #{q['order']['id']}", f"Date: {q['order']['order_date']}",
              f"Customer: {q['customer']['name']}", ""]
    for i in q["items"]:
        lines.append(f"{i['qty']} x {i['name']} @ {i['unit']} = {i['total']}")
        if i["description"]:
            lines.append("    " + i["description"].replace("\n", " "))
    lines += ["", f"Total: {q['total']}"]
    if q["order"]["amount_paid_c"]:
        lines += [f"Paid: {q['paid']}", f"Balance due: {q['balance']}"]
    if q["note"]:
        lines += ["", q["note"]]
    if q["contact"]:
        lines += ["", q["contact"]]
    return "\n".join(lines)


def send_mail(msg):
    """Send one message using the OT_SMTP_* settings. Tests replace this function."""
    host = os.environ["OT_SMTP_HOST"]
    port = int(os.environ.get("OT_SMTP_PORT", "587"))
    mode = os.environ.get("OT_SMTP_SECURITY", "starttls").lower()      # starttls | ssl | none
    user, pw = os.environ.get("OT_SMTP_USER", ""), os.environ.get("OT_SMTP_PASSWORD", "")
    ctx = ssl.create_default_context()
    if mode == "ssl":
        s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=20)
    with s:
        if mode == "starttls":
            s.starttls(context=ctx)
        if user:
            s.login(user, pw)
        s.send_message(msg)


@app.post("/api/orders/<int:oid>/email")
def email_quote(oid):
    if not email_ready():
        raise Bad("Email is not set up yet. Add the OT_SMTP_* settings to the container (see README).", 400)
    d = body()
    q = quote_data(oid)
    to = text(d.get("to"), "Email address", 120) or q["customer"]["email"]
    if not to:
        raise Bad("This customer has no email address. Enter one to send to.")
    if not EMAIL_RE.match(to):
        raise Bad("That email address does not look right")
    message = text(d.get("message"), "Message", 2000)
    msg = EmailMessage()
    msg["Subject"] = f"Quote for Order #{oid} from {BUSINESS_NAME}"
    msg["From"] = os.environ.get("OT_SMTP_FROM") or os.environ.get("OT_SMTP_USER") or "orders@localhost"
    msg["To"] = to
    if os.environ.get("OT_SMTP_REPLY_TO"):
        msg["Reply-To"] = os.environ["OT_SMTP_REPLY_TO"]
    msg.set_content(quote_text(q, message))
    msg.add_alternative(render_template("quote_email.html", message=message, **q), subtype="html")
    try:
        send_mail(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise Bad(f"The email could not be sent: {e}", 502)
    return jsonify(ok=True, to=to)


if __name__ == "__main__":
    app.run(host=os.environ.get("OT_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
