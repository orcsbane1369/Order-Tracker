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
- **Save & Add Another** (new orders only) saves the order and gives you a blank New Order window straight away, handy for entering several in a row. After **Add New** in Customers, Products and Categories the form also clears for the next entry.
- **Edit Order** (or double-click a row) opens the same window, so more items can be added later.
- **Move to** changes the stage of the selected order. Moving to Completed stamps the completion date.
- **Email Quote** sends the customer an email with the order's items and total. **Print Quote** opens a clean printable page.
- **Inventory:** each product has a **Current inventory** number. Stock comes off **when an order is entered**, so you can see
  what is really available when the next order comes in. It goes back if the order is cancelled or deleted, and only the
  difference moves if you edit the quantities. Changing stages (Pending, Shipped, Completed...) never touches stock, and Custom
  items with no product do not use any. In the order window each item row shows how many are on hand and how many will be left;
  if you are short it warns you in red but still lets you save (the stock then shows as short by that many).
  **Add Stock** (in Products) records a restock or correction, and **Stock History** lists every change: starting stock, orders
  entered/edited/cancelled/deleted, restocks and corrections.
- **Settings > Low-stock alerts:** set a number per product (pens at 25, shirts at 5...). At or below it, the product appears in a red
  box on the main screen and you get **one email** (to the address that sends your quotes). You get another email only after it has been
  restocked above its number and drops again. If email is not set up the red box still works.
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

Upgrading an older install keeps all its data. Products start with 0 in stock, and orders entered before the update are not
counted against stock, so enter each product's real count once under **Products** after updating.

## Backing up the databases (all three apps)
`deploy/backup-databases.sh` takes a safe snapshot of the Work Hours, Bills Tracker and Order Tracker databases into
`/mnt/user/Unraid_Backups/app-databases/<app>/` (one folder per app, files named like `ordertracker-2026-11-15-0300.db`), checks
each snapshot, and deletes snapshots older than 90 days. It uses SQLite's own backup call from inside each running container, so
it is safe while the apps are in use. A failed or skipped app makes the script report a problem, so one bad app never hides the others.

Set it up once with the **User Scripts** plugin (Settings > User Scripts; install it from Community Applications if needed):
1. **Add New Script**, name it `backup-app-databases`, click the gear > **Edit Script**, and paste in the contents of `deploy/backup-databases.sh`.
2. Set the schedule to **Custom** with `0 3 * * *` (3 AM every night) and Apply.
3. Click **Run Script** once and check the log says `All backups done.` and files appear in the `app-databases` folder.

It is set up for the containers `workhours`, `bills-tracker` and `ordertracker` and the share `Unraid_Backups` (capitals matter), as
checked on the server. A different name shows up as `SKIPPED` or an error in the log, and the settings are at the top of the script (`SHARE`, `KEEP_DAYS`, `APPS`).

To restore: stop the container, copy the snapshot you want over the app's database file in `/mnt/user/appdata/<app>/`
(`orders.db`, `bills.db` or `workhours.db`) keeping the same owner (`chown 99:100`), then start the container. Try one restore
on a spare copy now and then, so you know it works before you need it.

## Folder icon for the Unraid Docker tab
`deploy/unraid-folder-icon.png` (and `.svg`) is an icon for a Docker-tab folder holding the three apps. In Unraid, edit the folder and
set its icon to this URL (or upload the PNG):
`https://raw.githubusercontent.com/orcsbane1369/Order-Tracker/main/deploy/unraid-folder-icon.png`

## Tests
    python test_app.py          # customers, products, orders and items, stages, filters, stats, quotes, email, deletes
    python test_inventory.py    # stock in/out, cancel/edit/delete, restock log, low-stock alerts, upgrading an older database
