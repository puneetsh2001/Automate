"use strict";

const ALLOWED = [".pdf", ".png", ".jpg", ".jpeg"];
const FIELDS = [
  ["consumer_name", "Consumer Name"],
  ["account_number", "Account Number"],
  ["billing_period", "Billing Period"],
  ["due_date", "Due Date"],
  ["previous_reading", "Previous Reading"],
  ["current_reading", "Current Reading"],
  ["units_consumed", "Units Consumed (kWh)"],
  ["net_amount_due", "Net Amount Due (₹)"],
];

const $ = (id) => document.getElementById(id);
const form = $("upload-form");
const input = $("file-input");
const dropzone = $("dropzone");
const uploadBtn = $("upload-btn");
let selectedFile = null;

// ---------------------------------------------------------------- helpers
function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function formatDate(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split("-");
  return `${d}-${m}-${y}`;
}

function formatValue(key, value) {
  if (value === null || value === undefined) return null;
  if (key === "due_date") return formatDate(value);
  if (key === "net_amount_due") {
    return Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (typeof value === "number") return value.toLocaleString("en-IN", { maximumFractionDigits: 3 });
  return String(value);
}

async function apiError(response) {
  try {
    const body = await response.json();
    return body.message || body.detail || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status} ${response.statusText})`;
  }
}

// ------------------------------------------------------------- file select
function setFile(file) {
  hide($("error"));
  if (!file) return;
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED.includes(ext)) {
    showError(`Unsupported file type "${ext}". Please choose a PDF, PNG or JPG file.`);
    return;
  }
  selectedFile = file;
  $("dropzone-text").textContent = `${file.name} (${(file.size / 1024).toFixed(0)} KB)`;
  dropzone.classList.add("has-file");
  uploadBtn.disabled = false;
}

input.addEventListener("change", () => setFile(input.files[0]));
["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); }));
dropzone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

// ----------------------------------------------------------------- steps
function setStep(name, state) {
  const li = document.querySelector(`#steps li[data-step="${name}"]`);
  li.className = state;
}

function resetSteps() {
  $("steps").hidden = false;
  document.querySelectorAll("#steps li").forEach((li) => (li.className = ""));
}

function show(node) { node.hidden = false; }
function hide(node) { node.hidden = true; }

function showError(message) {
  $("error").textContent = message;
  show($("error"));
}

// ---------------------------------------------------------------- upload
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedFile) return;

  hide($("error"));
  hide($("result"));
  resetSteps();
  setStep("upload", "active");
  uploadBtn.disabled = true;

  const data = new FormData();
  data.append("file", selectedFile);

  try {
    const response = await fetch("/api/bills/upload", { method: "POST", body: data });
    if (!response.ok) throw new Error(await apiError(response));
    const bill = await response.json();

    // The server runs the pipeline in one request; reveal the completed stages in order.
    setStep("upload", "done");
    for (const step of ["ocr", "extract", "validate"]) {
      setStep(step, "active");
      await new Promise((r) => setTimeout(r, 180));
      setStep(step, "done");
    }
    renderBill(bill);
    loadHistory();
  } catch (err) {
    setStep("upload", "failed");
    showError(`Processing failed: ${err.message}`);
  } finally {
    uploadBtn.disabled = false;
  }
});

// ---------------------------------------------------------------- render
function renderBill(bill) {
  const details = bill.validation_details || {};
  const flagged = new Map((details.field_checks || [])
    .filter((c) => c.status !== "ok")
    .map((c) => [c.field, c]));

  const badge = $("status-badge");
  badge.textContent = bill.validation_status;
  badge.className = `badge badge-${bill.validation_status}`;

  const meta = [
    `#${bill.id}`,
    bill.original_filename,
    bill.extraction_method && `method: ${bill.extraction_method}`,
    bill.page_count && `${bill.page_count} page(s)`,
    bill.ocr_confidence != null && `OCR confidence ${bill.ocr_confidence.toFixed(0)}%`,
    bill.processing_time_ms != null && `${(bill.processing_time_ms / 1000).toFixed(1)} s`,
  ].filter(Boolean);
  $("result-meta").textContent = meta.join(" · ");

  const body = $("fields-body");
  body.replaceChildren();
  for (const [key, label] of FIELDS) {
    const value = formatValue(key, bill[key]);
    const tr = el("tr");
    tr.append(el("th", {}, label));
    const td = el("td", {}, value ?? "Not found");
    const check = flagged.get(key);
    if (value === null) td.className = "missing";
    else if (check) { td.className = "flagged"; td.title = check.message || check.status; }
    tr.append(td);
    body.append(tr);
  }
  const statusRow = el("tr");
  statusRow.append(el("th", {}, "Validation Status"));
  const statusCell = el("td");
  statusCell.append(el("span", { class: `badge badge-${bill.validation_status}` }, bill.validation_status));
  statusRow.append(statusCell);
  body.append(statusRow);

  const mc = $("meter-check");
  const formula = details.calculation ? ` (${details.calculation})` : "";
  if (details.meter_reading_check === true) {
    mc.textContent = `✓ Meter check passed: calculated units match the reported ${details.reported_units} kWh${formula}.`;
  } else if (details.meter_reading_check === false) {
    mc.textContent = `✕ Meter check failed: calculated ${details.calculated_units}${formula}, ` +
      `reported ${details.reported_units ?? "—"} (difference ${details.difference ?? "—"}).`;
  } else if (details.calculation && bill.field_sources?.units_consumed?.startsWith("derived:")) {
    mc.textContent = `Units consumed is not printed on the bill; calculated from the readings${formula}.`;
  } else {
    mc.textContent = "Meter check not possible (readings or units missing).";
  }

  const notes = details.notes || [];
  $("notes-list").replaceChildren(...notes.map((n) => el("li", {}, n)));
  notes.length ? show($("notes")) : hide($("notes"));

  const list = $("warnings-list");
  list.replaceChildren(...(bill.warnings || []).map((w) => el("li", {}, w)));
  (bill.warnings || []).length ? show($("warnings")) : hide($("warnings"));

  $("raw-text").textContent = bill.raw_ocr_text || "(no text)";
  show($("result"));
}

// --------------------------------------------------------------- history
async function loadHistory() {
  const tbody = $("history-body");
  try {
    const response = await fetch("/api/bills?limit=20");
    if (!response.ok) throw new Error(await apiError(response));
    const data = await response.json();
    if (!data.items.length) {
      tbody.replaceChildren(el("tr", {}, null));
      tbody.firstChild.append(el("td", { colspan: 9, class: "muted" }, "No bills processed yet."));
      return;
    }
    tbody.replaceChildren(...data.items.map(historyRow));
  } catch (err) {
    tbody.replaceChildren(el("tr"));
    tbody.firstChild.append(el("td", { colspan: 9, class: "muted" }, `Could not load bills: ${err.message}`));
  }
}

function historyRow(b) {
  const tr = el("tr", { title: "Show details" });
  const cells = [
    [b.id], [b.original_filename, "file"], [b.consumer_name ?? "—"], [b.account_number ?? "—"],
    [b.billing_period ?? "—"], [formatValue("units_consumed", b.units_consumed) ?? "—", "num"],
    [formatValue("net_amount_due", b.net_amount_due) ?? "—", "num"],
  ];
  for (const [text, cls] of cells) {
    tr.append(el("td", cls ? { class: cls, title: String(text) } : {}, text));
  }
  const status = el("td");
  status.append(el("span", { class: `badge badge-${b.validation_status}` }, b.validation_status));
  tr.append(status);

  const actions = el("td");
  const del = el("button", { type: "button", class: "link" }, "Delete");
  del.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete bill #${b.id}?`)) return;
    const response = await fetch(`/api/bills/${b.id}`, { method: "DELETE" });
    if (!response.ok) return showError(await apiError(response));
    loadHistory();
  });
  actions.append(del);
  tr.append(actions);

  tr.addEventListener("click", async () => {
    const response = await fetch(`/api/bills/${b.id}`);
    if (!response.ok) return showError(await apiError(response));
    hide($("steps"));
    renderBill(await response.json());
    $("result").scrollIntoView({ behavior: "smooth" });
  });
  return tr;
}

async function loadHealth() {
  const node = $("health");
  try {
    const response = await fetch("/health");
    const h = await response.json();
    node.textContent = h.status === "ok" ? "● service ok" : `● degraded (db: ${h.database}, ocr: ${h.ocr})`;
    node.className = `health ${h.status === "ok" ? "ok" : "bad"}`;
    if (h.ocr_detail) node.title = h.ocr_detail;
  } catch {
    node.textContent = "● server unreachable";
    node.className = "health bad";
  }
}

$("refresh-btn").addEventListener("click", loadHistory);
loadHealth();
loadHistory();
