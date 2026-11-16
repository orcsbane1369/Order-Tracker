# Order Tracker

A web version of the desktop Order Tracker for our home business. Flask + SQLite, runs as a Docker container on Unraid,
built and updated the same way as the Work Hours and Bills Tracker apps. **Home network only: there is no login, so never
forward its port on your router or put it behind a public tunnel.**

## What it does
- **Stage boxes** across the top count the orders in each stage (New, Pending, Ready for Pickup, Shipped, Completed,
  Cancelled) and the balance still owed. Click a box to filter the list to that stage; click again to clear.
- **New Order** opens one window: pick the customer from the drop-down (or choose **+ New customer...** and fill in their
  details), set the date, stage, payment method and amount paid, then add items. Each item is a product (price fills in
  automatically, you can change it) or a **Custom item**, plus customization notes and a quantity. The total, amount paid
  and balance due update as you type.
- **Edit Order** (or double-click a row) opens the same window, so more items can be added later.
- **Move to** changes the stage of the selected order. Moving to Completed stamps the completion date.
- **Email Quote** sends the customer an email with the order's items and total. **Print Quote** opens a clean printable page.
- **Customers**, **Products** (with Categories) and **Order Stats** (per customer: orders, open, completed, ordered, paid,
  balance due) are the other windows from the old program.
- The order list **scrolls inside its own box**, with sort-by-column and filters for order #, customer, stage and dates.
- Old orders keep the item names and prices they were sold at, even if a product is later renamed, repriced or deleted.

## Run locally
    python -m venv .venv
    .venv\Scripts\activate          (Windows)   |   source .venv/bin/activate  (Mac/Linux)
    pip install -r requirements.txt
    python app.py
Open http://127.0.0.1:5000. Data lives in `instance/orders.db` (one file; back it up by copying it).

## Settings (environment variables)
- `OT_BUSINESS_NAME` shown in the header and on quotes (default Our Creations). `OT_BUSINESS_CONTACT` and `OT_QUOTE_NOTE` are optional extra lines on quotes.
- `OT_SMTP_HOST`, `OT_SMTP_PORT` (587), `OT_SMTP_SECURITY` (`starttls`, `ssl` or `none`), `OT_SMTP_USER`, `OT_SMTP_PASSWORD`,
  `OT_SMTP_FROM`, `OT_SMTP_REPLY_TO` turn on the **Email Quote** button. With `OT_SMTP_HOST` empty the button explains that email is not set up.
  For Gmail use `smtp.gmail.com`, port 587, `starttls`, your address, and a Google **App password** (not your normal password).
- `OT_INSTANCE=/data` folder for the database (the Docker volume). `OT_DB=path` use another file.
- Docker/Unraid: `OT_PORT` (host port, default 5007) and `OT_DATA_DIR` live in `.env` (copy `.env.example`).

## Deploy on Unraid
GitHub builds the image on every push (`.github/workflows/docker.yml`) and publishes
`ghcr.io/orcsbane1369/order-tracker:latest` (set the package to **Public** once under your profile > Packages).

Add it in the Docker tab with **Add Container**:
- Repository `ghcr.io/orcsbane1369/order-tracker:latest`, network bridge
- Port: host 5007 -> container 5000
- Path: `/mnt/user/appdata/ordertracker` -> `/data`
- Variables: `OT_BUSINESS_NAME`, and the `OT_SMTP_*` ones if you want email
- Extra Parameters: `--user 99:100 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`
- Icon URL: `https://raw.githubusercontent.com/orcsbane1369/Order-Tracker/main/static/icon.png`

(Or use `docker-compose.yml` with Compose Manager.) To update after a push: Docker tab > **Check for Updates** > **apply update**.
Back up by copying `/mnt/user/appdata/ordertracker/orders.db`.

## Tests
    python test_app.py    # customers, products, orders and items, stages, filters, stats, quotes, email, deletes
