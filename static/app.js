(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { cfg: null, customers: [], categories: [], products: [], orders: [], stats: null,
                  sel: null, sort: { k: "id", dir: -1 }, editing: null, selCust: null, selProd: null, selCat: null };

  // ---------- helpers
  async function api(method, url, body) {
    const r = await fetch(url, {
      method, headers: { "Content-Type": "application/json", "X-Requested-With": "order-tracker" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) throw new Error(data.error || "Something went wrong (" + r.status + ")");
    return data;
  }
  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "class") { if (v) n.className = v; } else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else if (v !== false && v !== null && v !== undefined) n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid) n.append(kid);
    return n;
  }
  const pad = (n) => String(n).padStart(2, "0");
  const todayISO = () => { const d = new Date(); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); };
  const mdy = (iso) => { if (!iso) return ""; const [y, m, d] = iso.split("-"); return Number(m) + "/" + Number(d) + "/" + y; };
  const usd = (s) => {
    const n = parseFloat(s || 0);
    const t = "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return n < 0 ? "-" + t : t;
  };
  const slug = (s) => s.split(" ")[0];
  let toastTimer;
  function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3200); }
  const openDlg = (d) => { if (!d.open) d.showModal(); };
  document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) d.close(); }));
  const setOptions = (sel, opts, value) => {
    sel.replaceChildren(...opts.map(([v, t]) => el("option", { value: v, text: t })));
    if (value !== undefined) sel.value = value;
  };

  // ---------- loading
  async function loadLists() {
    [state.customers, state.categories, state.products] = await Promise.all([api("GET", "/api/customers"), api("GET", "/api/categories"), api("GET", "/api/products")]);
    setOptions($("fCust"), [["", "All customers"], ...state.customers.map((c) => [c.id, c.name])], $("fCust").value);
  }
  function filterQuery() {
    const p = new URLSearchParams();
    const m = { order_no: $("fNo").value.trim(), customer_id: $("fCust").value, status: $("fStatus").value, start: $("fStart").value, end: $("fEnd").value };
    for (const [k, v] of Object.entries(m)) if (v) p.set(k, v);
    return p.toString();
  }
  async function loadOrders() {
    try { state.orders = await api("GET", "/api/orders?" + filterQuery()); $("count").textContent = ""; }
    catch (e) { state.orders = []; $("count").textContent = e.message; }
    if (!state.orders.some((o) => o.id === state.sel)) state.sel = null;
    renderTable();
  }
  async function loadStats() { state.stats = await api("GET", "/api/stats"); renderCards(); }
  async function refresh() { await Promise.all([loadLists(), loadOrders(), loadStats()]); }

  // ---------- stage cards
  function renderCards() {
    const box = $("cards"); box.replaceChildren();
    for (const s of state.stats.by_status) {
      const b = el("button", { class: "card s-" + slug(s.status), type: "button", "aria-pressed": String($("fStatus").value === s.status), onclick: () => {
        $("fStatus").value = $("fStatus").value === s.status ? "" : s.status; renderCards(); loadOrders(); } },
        el("span", { class: "lbl", text: s.status }), el("strong", { text: String(s.count) }),
        el("span", { class: "amt", text: s.status === "Cancelled" ? " " : usd(s.balance) + " due" }));
      box.append(b);
    }
    box.append(el("div", { class: "card static" }, el("span", { class: "lbl", text: "Balance owed (all active)" }), el("strong", { text: usd(state.stats.outstanding) }), el("span", { class: "amt", text: " " })));
  }

  // ---------- order table
  function sortVal(o, k) {
    if (["id"].includes(k)) return o.id;
    if (["total", "amount_paid", "balance"].includes(k)) return parseFloat(o[k]);
    return (o[k] || "").toString().toLowerCase();
  }
  function renderTable() {
    const { k, dir } = state.sort;
    const rows = [...state.orders].sort((x, y) => { const a = sortVal(x, k), b = sortVal(y, k); return (a < b ? -1 : a > b ? 1 : 0) * dir || y.id - x.id; });
    const body = $("orderBody"); body.replaceChildren();
    for (const o of rows) {
      const tr = el("tr", { "data-id": o.id, tabindex: "0", "aria-selected": String(o.id === state.sel) });
      const td = (t, cls) => tr.append(el("td", { text: t, class: cls || false }));
      td(String(o.id), "n"); td(o.customer_name); td(mdy(o.order_date)); td(usd(o.total), "n"); td(usd(o.amount_paid), "n");
      td(usd(o.balance), "n" + (parseFloat(o.balance) > 0 && o.status !== "Cancelled" ? " neg" : ""));
      td(o.payment_method);
      tr.append(el("td", {}, el("span", { class: "pill s-" + slug(o.status), text: o.status })));
      td(mdy(o.completion_date));
      body.append(tr);
    }
    $("empty").hidden = rows.length > 0;
    $("count").textContent = rows.length + (rows.length === 1 ? " order" : " orders");
    document.querySelectorAll("#orderTable th").forEach((th) => { if (th.dataset.k === k) th.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending"); else th.removeAttribute("aria-sort"); });
    updateButtons();
  }
  const selected = () => state.orders.find((o) => o.id === state.sel);
  function updateButtons() {
    const o = selected(), has = !!o;
    for (const id of ["btnEdit", "btnEmail", "btnPrint", "btnDelete", "selStage"]) $(id).disabled = !has;
    $("selStage").value = has ? o.status : "";
  }
  // Selecting only toggles the highlight (no rebuild), so a double-click lands on the same row element.
  function selectRow(id) {
    state.sel = id;
    document.querySelectorAll("#orderBody tr").forEach((tr) => tr.setAttribute("aria-selected", String(Number(tr.dataset.id) === id)));
    updateButtons();
  }
  $("orderBody").addEventListener("click", (e) => { const tr = e.target.closest("tr"); if (tr) selectRow(Number(tr.dataset.id)); });
  $("orderBody").addEventListener("dblclick", (e) => { if (e.target.closest("tr")) openOrder(state.sel); });
  $("orderBody").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.closest("tr")) { selectRow(Number(e.target.closest("tr").dataset.id)); openOrder(state.sel); } });
  $("orderTable").querySelector("thead").addEventListener("click", (e) => {
    const th = e.target.closest("th"); if (!th) return;
    state.sort = { k: th.dataset.k, dir: state.sort.k === th.dataset.k ? -state.sort.dir : 1 }; renderTable();
  });

  // ---------- filters
  let ft; const later = () => { clearTimeout(ft); ft = setTimeout(() => { renderCards(); loadOrders(); }, 250); };
  ["fNo", "fStart", "fEnd"].forEach((id) => $(id).addEventListener("input", later));
  ["fCust", "fStatus"].forEach((id) => $(id).addEventListener("change", later));
  $("btnClear").addEventListener("click", () => { ["fNo", "fCust", "fStatus", "fStart", "fEnd"].forEach((id) => { $(id).value = ""; }); renderCards(); loadOrders(); });

  // ---------- toolbar actions
  $("btnNew").addEventListener("click", () => openOrder(null));
  $("btnEdit").addEventListener("click", () => openOrder(state.sel));
  $("btnPrint").addEventListener("click", () => { window.open("/orders/" + state.sel + "/quote", "_blank", "noopener"); });
  $("selStage").addEventListener("change", async () => {
    try { await api("POST", "/api/orders/" + state.sel + "/status", { status: $("selStage").value, completion_date: todayISO() }); toast("Order #" + state.sel + " moved to " + $("selStage").value); await Promise.all([loadOrders(), loadStats()]); }
    catch (e) { toast(e.message); updateButtons(); }
  });
  $("btnDelete").addEventListener("click", async () => {
    const o = selected(); if (!o || !confirm("Delete order #" + o.id + " for " + o.customer_name + "? This cannot be undone.")) return;
    try { await api("DELETE", "/api/orders/" + o.id); state.sel = null; toast("Order deleted"); await Promise.all([loadOrders(), loadStats(), loadLists()]); } catch (e) { toast(e.message); }
  });

  // ---------- new / edit order window
  const recalc = () => {
    let total = 0;
    document.querySelectorAll("#itemBody tr").forEach((tr) => {
      const q = parseInt(tr.querySelector(".i-qty").value, 10) || 0, p = parseFloat(tr.querySelector(".i-price").value) || 0;
      const t = Math.round(q * p * 100) / 100; total += t; tr.querySelector(".i-total").textContent = usd(t);
    });
    const paid = parseFloat($("o_paid").value) || 0;
    $("t_total").textContent = usd(total); $("t_paid").textContent = usd(paid); $("t_bal").textContent = usd(total - paid);
  };
  function productOptions() {
    const groups = new Map();
    for (const p of state.products) { const g = p.category || "No category"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(p); }
    const out = [el("option", { value: "", text: "Custom item" })];
    for (const [g, ps] of groups) out.push(el("optgroup", { label: g }, ...ps.map((p) => el("option", { value: p.id, text: p.name + " (" + usd(p.unit_price) + ")" }))));
    return out;
  }
  function addItemRow(it) {
    it = it || { product_id: "", product_name: "", description: "", qty: 1, unit_price: "" };
    const sel = el("select", { class: "i-prod", "aria-label": "Product" }, ...productOptions());
    sel.value = it.product_id && state.products.some((p) => p.id === it.product_id) ? String(it.product_id) : "";
    const tr = el("tr", {});
    tr.dataset.pname = it.product_name || "";
    sel.addEventListener("change", () => {
      const p = state.products.find((x) => String(x.id) === sel.value);
      tr.dataset.pname = p ? p.name : "";
      if (p) { tr.querySelector(".i-price").value = p.unit_price; if (!tr.querySelector(".i-desc").value && p.description) tr.querySelector(".i-desc").placeholder = p.description; }
      recalc();
    });
    tr.append(el("td", {}, sel),
      el("td", {}, el("input", { class: "i-desc", maxlength: "1000", value: it.description, placeholder: "Color, size, name to print...", "aria-label": "Customization" })),
      el("td", {}, el("input", { class: "i-qty", type: "number", min: "1", step: "1", value: String(it.qty), "aria-label": "Quantity", oninput: recalc })),
      el("td", {}, el("input", { class: "i-price", type: "number", min: "0", step: "0.01", value: it.unit_price, "aria-label": "Unit price", oninput: recalc })),
      el("td", { class: "n i-total", text: "$0.00" }),
      el("td", {}, el("button", { type: "button", class: "ghost", "aria-label": "Remove item", text: "✕", onclick: () => { tr.remove(); recalc(); } })));
    $("itemBody").append(tr); recalc(); return tr;
  }
  $("btnAddItem").addEventListener("click", () => addItemRow().querySelector(".i-prod").focus());
  $("o_paid").addEventListener("input", recalc);
  $("o_cust").addEventListener("change", () => { $("newCust").hidden = $("o_cust").value !== "__new"; if (!$("newCust").hidden) $("nc_name").focus(); });

  async function openOrder(id) {
    $("orderErr").textContent = "";
    const cfg = state.cfg;
    setOptions($("o_status"), cfg.statuses.map((s) => [s, s]));
    setOptions($("o_method"), [["", "(none yet)"], ...cfg.payment_methods.map((m) => [m, m])]);
    setOptions($("o_cust"), [["", "Select a customer..."], ...state.customers.map((c) => [c.id, c.name]), ["__new", "+ New customer..."]]);
    ["nc_name", "nc_phone", "nc_email", "nc_addr", "o_notes"].forEach((f) => { $(f).value = ""; });
    $("itemBody").replaceChildren(); state.editing = id;
    if (id) {
      let o; try { o = await api("GET", "/api/orders/" + id); } catch (e) { toast(e.message); return; }
      $("orderTitle").textContent = "Order #" + id;
      $("o_cust").value = String(o.customer_id); $("o_date").value = o.order_date; $("o_status").value = o.status;
      $("o_method").value = o.payment_method; $("o_paid").value = o.amount_paid; $("o_notes").value = o.notes;
      o.items.forEach((it) => addItemRow(it));
    } else {
      $("orderTitle").textContent = "New Order";
      $("o_cust").value = ""; $("o_date").value = todayISO(); $("o_status").value = "New"; $("o_method").value = ""; $("o_paid").value = "0";
      addItemRow();
    }
    $("btnSaveAnother").hidden = !!id;   // only makes sense when entering new orders
    $("newCust").hidden = true; recalc(); openDlg($("orderDlg"));
    if (!id) $("o_cust").focus();
  }
  async function saveOrder(another) {
    $("orderErr").textContent = "";
    if (!$("orderForm").reportValidity()) return;
    const body = {
      order_date: $("o_date").value, status: $("o_status").value, payment_method: $("o_method").value,
      amount_paid: $("o_paid").value || "0", notes: $("o_notes").value,
      items: [...document.querySelectorAll("#itemBody tr")].map((tr) => ({
        product_id: tr.querySelector(".i-prod").value || null, product_name: tr.dataset.pname, description: tr.querySelector(".i-desc").value,
        qty: tr.querySelector(".i-qty").value, unit_price: tr.querySelector(".i-price").value || "0" })),
    };
    if ($("o_cust").value === "__new") body.new_customer = { name: $("nc_name").value, phone: $("nc_phone").value, email: $("nc_email").value, address: $("nc_addr").value };
    else body.customer_id = $("o_cust").value;
    if (!body.customer_id && !body.new_customer) { $("orderErr").textContent = "Pick a customer or choose + New customer."; return; }
    $("btnSave").disabled = $("btnSaveAnother").disabled = true;   // no double-clicking into two orders
    try {
      const r = state.editing ? await api("PUT", "/api/orders/" + state.editing, body) : await api("POST", "/api/orders", body);
      state.sel = state.editing || r.id; toast("Order #" + state.sel + " saved");
      await refresh();
      if (another) await openOrder(null); else $("orderDlg").close();   // a blank order, ready for the next one
    } catch (err) { $("orderErr").textContent = err.message; }
    $("btnSave").disabled = $("btnSaveAnother").disabled = false;
  }
  $("orderForm").addEventListener("submit", (e) => { e.preventDefault(); saveOrder(false); });
  $("btnSaveAnother").addEventListener("click", () => saveOrder(true));

  // ---------- email quote
  $("btnEmail").addEventListener("click", () => {
    const o = selected(); if (!o) return;
    $("mailErr").textContent = ""; $("m_msg").value = "";
    $("m_to").value = o.customer_email || "";
    $("mailInfo").textContent = "Sends the quote for order #" + o.id + " (" + usd(o.total) + ") to " + o.customer_name + ".";
    $("mailSend").disabled = !state.cfg.email_enabled;
    if (!state.cfg.email_enabled) $("mailErr").textContent = "Email is not set up yet. Add the OT_SMTP_* settings (see README).";
    openDlg($("mailDlg"));
  });
  $("mailForm").addEventListener("submit", async (e) => {
    e.preventDefault(); $("mailErr").textContent = ""; $("mailSend").disabled = true;
    try { const r = await api("POST", "/api/orders/" + state.sel + "/email", { to: $("m_to").value, message: $("m_msg").value }); $("mailDlg").close(); toast("Quote sent to " + r.to); }
    catch (err) { $("mailErr").textContent = err.message; }
    $("mailSend").disabled = !state.cfg.email_enabled;
  });

  // ---------- customers
  const cfields = ["c_name", "c_phone", "c_email", "c_addr"];
  function renderCustList() {
    const box = $("custList"); box.replaceChildren();
    for (const c of state.customers) box.append(el("button", { type: "button", role: "option", "aria-selected": String(c.id === state.selCust), text: c.name + (c.orders ? "  (" + c.orders + ")" : ""),
      onclick: () => { state.selCust = c.id; $("c_name").value = c.name; $("c_phone").value = c.phone; $("c_email").value = c.email; $("c_addr").value = c.address; renderCustList(); } }));
    $("cUpd").disabled = $("cDel").disabled = state.selCust === null;
  }
  const custBody = () => ({ name: $("c_name").value, phone: $("c_phone").value, email: $("c_email").value, address: $("c_addr").value });
  async function custDo(fn, msg) {
    $("custErr").textContent = "";
    try { await fn(); toast(msg); await refresh(); renderCustList(); } catch (e) { $("custErr").textContent = e.message; }
  }
  $("btnCust").addEventListener("click", () => { state.selCust = null; cfields.forEach((f) => { $(f).value = ""; }); $("custErr").textContent = ""; renderCustList(); openDlg($("custDlg")); });
  $("cAdd").addEventListener("click", () => custDo(async () => {
    await api("POST", "/api/customers", custBody());
    state.selCust = null; cfields.forEach((f) => { $(f).value = ""; }); $("c_name").focus();   // ready for the next customer
  }, "Customer added"));
  $("cUpd").addEventListener("click", () => custDo(() => api("PUT", "/api/customers/" + state.selCust, custBody()), "Customer updated"));
  $("cDel").addEventListener("click", () => { const c = state.customers.find((x) => x.id === state.selCust); if (c && confirm("Delete customer " + c.name + "?")) custDo(async () => { await api("DELETE", "/api/customers/" + c.id); state.selCust = null; cfields.forEach((f) => { $(f).value = ""; }); }, "Customer deleted"); });
  $("cClr").addEventListener("click", () => { state.selCust = null; cfields.forEach((f) => { $(f).value = ""; }); renderCustList(); });

  // ---------- products + categories
  function showTab(p) { $("paneP").hidden = !p; $("paneC").hidden = p; $("tabP").setAttribute("aria-selected", String(p)); $("tabC").setAttribute("aria-selected", String(!p)); }
  $("tabP").addEventListener("click", () => showTab(true)); $("tabC").addEventListener("click", () => showTab(false));
  function catOptions(allLabel) { return [["", allLabel], ...state.categories.map((c) => [c.id, c.name])]; }
  function clearProd() { state.selProd = null; ["p_name", "p_desc", "p_price"].forEach((f) => { $(f).value = ""; }); $("p_cat").value = ""; }
  function renderProd() {
    setOptions($("p_filter"), catOptions("-- All Categories --"), $("p_filter").value);
    setOptions($("p_cat"), catOptions("(no category)"), $("p_cat").value);
    const f = $("p_filter").value, box = $("prodList"); box.replaceChildren();
    for (const p of state.products.filter((x) => !f || String(x.category_id) === f))
      box.append(el("button", { type: "button", role: "option", "aria-selected": String(p.id === state.selProd), text: p.name + (p.category ? " (" + p.category + ")" : "") + " (" + usd(p.unit_price) + ")",
        onclick: () => { state.selProd = p.id; $("p_name").value = p.name; $("p_desc").value = p.description; $("p_price").value = p.unit_price; $("p_cat").value = p.category_id || ""; renderProd(); } }));
    $("pUpd").disabled = $("pDel").disabled = state.selProd === null;
    const cbox = $("catList"); cbox.replaceChildren();
    for (const c of state.categories) cbox.append(el("button", { type: "button", role: "option", "aria-selected": String(c.id === state.selCat), text: c.name,
      onclick: () => { state.selCat = c.id; $("k_name").value = c.name; renderProd(); } }));
    $("kUpd").disabled = $("kDel").disabled = state.selCat === null;
  }
  const prodBody = () => ({ name: $("p_name").value, description: $("p_desc").value, unit_price: $("p_price").value || "0", category_id: $("p_cat").value });
  async function prodDo(fn, msg, errId) {
    $(errId).textContent = "";
    try { await fn(); toast(msg); await loadLists(); renderProd(); } catch (e) { $(errId).textContent = e.message; }
  }
  $("btnProd").addEventListener("click", () => { clearProd(); state.selCat = null; $("k_name").value = ""; $("prodErr").textContent = $("catErr").textContent = ""; showTab(true); renderProd(); openDlg($("prodDlg")); });
  $("p_filter").addEventListener("change", renderProd);
  $("pAdd").addEventListener("click", () => prodDo(async () => {
    await api("POST", "/api/products", prodBody());
    clearProd(); $("p_name").focus();   // ready for the next product
  }, "Product added", "prodErr"));
  $("pUpd").addEventListener("click", () => prodDo(() => api("PUT", "/api/products/" + state.selProd, prodBody()), "Product updated", "prodErr"));
  $("pDel").addEventListener("click", () => { if (confirm("Delete this product? Old orders keep their item names and prices.")) prodDo(async () => { await api("DELETE", "/api/products/" + state.selProd); clearProd(); }, "Product deleted", "prodErr"); });
  $("pClr").addEventListener("click", () => { clearProd(); renderProd(); });
  $("kAdd").addEventListener("click", () => prodDo(async () => {
    await api("POST", "/api/categories", { name: $("k_name").value });
    state.selCat = null; $("k_name").value = ""; $("k_name").focus();   // ready for the next category
  }, "Category added", "catErr"));
  $("kUpd").addEventListener("click", () => prodDo(() => api("PUT", "/api/categories/" + state.selCat, { name: $("k_name").value }), "Category updated", "catErr"));
  $("kDel").addEventListener("click", () => { if (confirm("Delete this category? Its products stay, with no category.")) prodDo(async () => { await api("DELETE", "/api/categories/" + state.selCat); state.selCat = null; $("k_name").value = ""; }, "Category deleted", "catErr"); });
  $("kClr").addEventListener("click", () => { state.selCat = null; $("k_name").value = ""; renderProd(); });

  // ---------- stats
  $("btnStats").addEventListener("click", async () => {
    try { await loadStats(); } catch (e) { toast(e.message); return; }
    const body = $("statsBody"); body.replaceChildren();
    for (const c of state.stats.customers) {
      const tr = el("tr", {}); const td = (t, cls) => tr.append(el("td", { text: t, class: cls || false }));
      td(c.name); td(String(c.orders), "n"); td(String(c.open), "n"); td(String(c.completed), "n"); td(usd(c.ordered), "n"); td(usd(c.paid), "n"); td(usd(c.balance), "n");
      body.append(tr);
    }
    $("statsOwed").textContent = "Balance owed across all active orders: " + usd(state.stats.outstanding);
    openDlg($("statsDlg"));
  });

  // ---------- start
  (async () => {
    try {
      state.cfg = await api("GET", "/api/config");
      setOptions($("fStatus"), [["", "All stages"], ["Open", "Open (not completed or cancelled)"], ...state.cfg.statuses.map((s) => [s, s])]);
      setOptions($("selStage"), [["", "Move to..."], ...state.cfg.statuses.map((s) => [s, s])]);
      await refresh();
    } catch (e) { $("count").textContent = e.message; }
  })();
})();
