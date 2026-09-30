"use strict";

// ------------------------------------------------------------------ config
const ALLOWED = [".pdf", ".png", ".jpg", ".jpeg"];
const FIELDS = [
  ["consumer_name", "Consumer name"],
  ["account_number", "Account number"],
  ["billing_period", "Billing period"],
  ["due_date", "Due date"],
  ["previous_reading", "Previous reading"],
  ["current_reading", "Current reading"],
  ["units_consumed", "Units consumed"],
  ["net_amount_due", "Net amount due"],
];
const STATUS_LABELS = { VALID: "Valid", WARNING: "Warning", INVALID: "Invalid" };
const METHOD_LABELS = { text: "Text PDF", ocr: "OCR", mixed: "Text + OCR" };
const PROVIDER_NAMES = { apdcl: "APDCL", rajasthan: "Rajasthan", karnataka: "Karnataka" };
const HISTORY_PAGE_SIZE = 10;
const STEPS = ["upload", "ocr", "extract", "validate"];

// Static icon paths (24x24, stroked)
const ICONS = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  error: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
  calc: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>',
};

// ------------------------------------------------------------------- state
const $ = (id) => document.getElementById(id);
const input = $("file-input");
const dropzone = $("dropzone");
const uploadBtn = $("upload-btn");

const state = {
  file: null,          // file chosen in the drop zone
  bill: null,          // bill shown in the result panel
  page: 1,             // preview page
  previewUrl: null,    // object URL of the preview image
  previewRequest: 0,   // ignores responses of superseded preview requests
  localFile: null,     // the uploaded file, for previewing a result that was not saved
  localUrl: null,      // object URL of localFile
  timer: null,         // progress clock
  history: { offset: 0, status: "", search: "", total: 0, request: 0 },
};

// ----------------------------------------------------------------- helpers
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined && child !== false) node.append(child);  // strings become text nodes
  }
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24", width: 16, height: 16, fill: "none", stroke: "currentColor", "stroke-width": 2,
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  })) svg.setAttribute(key, value);
  svg.innerHTML = ICONS[name];  // constant markup only
  return svg;
}

function show(node) { node.hidden = false; }
function hide(node) { node.hidden = true; }

function fmtNumber(value) {
  return value === null || value === undefined ? null : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 3 });
}

function fmtAmount(value) {
  if (value === null || value === undefined) return null;
  return "₹" + Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Fixed month names: browsers' "short" month differs by locale ("Sep" / "Sept")
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parseIsoDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

// parts: "dmy" -> 15 Aug 2025, "dm" -> 15 Aug, "my" -> Aug 2025
function fmtDate(iso, parts = "dmy") {
  if (!iso) return null;
  const date = parseIsoDate(iso);
  const day = String(date.getUTCDate()).padStart(2, "0");
  const month = MONTHS[date.getUTCMonth()];
  const year = date.getUTCFullYear();
  return { dmy: `${day} ${month} ${year}`, dm: `${day} ${month}`, my: `${month} ${year}` }[parts];
}

// "2025-07-01 to 2025-07-31" -> "01 Jul 2025 – 31 Jul 2025" (or "Jul 2025" when short and a whole month)
function fmtPeriod(period, short = false) {
  if (!period) return null;
  const m = /^(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})$/.exec(period);
  if (!m) return period;
  const start = parseIsoDate(m[1]);
  const end = parseIsoDate(m[2]);
  const lastDay = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, 0)).getUTCDate();
  const wholeMonth = start.getUTCDate() === 1 && end.getUTCDate() === lastDay
    && start.getUTCMonth() === end.getUTCMonth() && start.getUTCFullYear() === end.getUTCFullYear();
  if (short && wholeMonth) return fmtDate(m[1], "my");
  const sameYear = start.getUTCFullYear() === end.getUTCFullYear();
  return `${fmtDate(m[1], sameYear ? "dm" : "dmy")} – ${fmtDate(m[2])}`;
}

function fmtSize(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function relativeTime(iso) {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} d ago`;
  const date = new Date(iso);
  return `${String(date.getDate()).padStart(2, "0")} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

function fileTypeLabel(type) {
  return { pdf: "PDF", png: "PNG", jpeg: "JPG", jpg: "JPG" }[type] || String(type || "").toUpperCase();
}

async function readError(response) {
  try {
    const body = await response.json();
    return { ...body, message: body.message || body.detail || `Request failed (${response.status})` };
  } catch {
    return { message: `Request failed (${response.status} ${response.statusText})` };
  }
}

function toast(message, kind = "ok") {
  const node = el("div", { class: `toast ${kind}`, role: "status" }, icon(kind === "error" ? "error" : "check"), message);
  $("toasts").append(node);
  setTimeout(() => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 250);
  }, kind === "error" ? 5000 : 2800);
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} copied`);
  } catch {
    toast("Could not copy to the clipboard", "error");
  }
}

// ------------------------------------------------------------- file select
function setFile(file) {
  hide($("error"));
  if (!file) return;
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!ALLOWED.includes(ext)) {
    showError(`"${file.name}" is not a PDF, PNG or JPG file.`);
    return;
  }
  state.file = file;
  const type = ext === ".jpeg" ? "jpg" : ext.slice(1);
  $("file-type").textContent = fileTypeLabel(type);
  $("file-type").dataset.type = type;
  $("file-name").textContent = file.name;
  $("file-name").title = file.name;
  $("file-size").textContent = fmtSize(file.size);
  hide($("dz-empty"));
  show($("dz-file"));
  dropzone.classList.add("has-file");
  uploadBtn.disabled = false;
}

function resetDropzone() {
  state.file = null;
  input.value = "";  // so choosing the same file again still fires "change"
  show($("dz-empty"));
  hide($("dz-file"));
  dropzone.classList.remove("has-file");
  uploadBtn.disabled = true;
}

input.addEventListener("change", () => setFile(input.files[0]));
dropzone.addEventListener("dragenter", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragleave", (e) => {
  if (!dropzone.contains(e.relatedTarget)) dropzone.classList.remove("dragover");
});
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  setFile(e.dataTransfer.files[0]);
});
// A file dropped next to the drop zone must not make the browser navigate away from the app
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

$("file-clear").addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  hide($("error"));
  resetDropzone();
});

function showError(message) {
  $("error-text").textContent = message;
  show($("error"));
}

// ---------------------------------------------------------------- progress
function setStep(name, value) {
  document.querySelector(`#steps li[data-step="${name}"]`).className = value;
}

function startClock(label) {
  clearInterval(state.timer);
  const started = Date.now();
  const tick = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    const hint = seconds >= 10 ? " · scanned pages take longer" : "";
    $("progress-text").textContent = `${label} ${seconds}s${hint}`;
  };
  tick();
  state.timer = setInterval(tick, 1000);
}

function stopClock() {
  clearInterval(state.timer);
  state.timer = null;
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// XMLHttpRequest instead of fetch: it reports when the upload itself has finished,
// so the "Upload" step completes before the server starts reading the document.
function postFile(url, file, onProgress, onUploaded) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.addEventListener("progress", (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); });
    xhr.upload.addEventListener("load", onUploaded);
    xhr.addEventListener("load", () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON error page */ }
      resolve({ status: xhr.status, body });
    });
    xhr.addEventListener("error", () => reject(new Error("The server could not be reached. Check your connection.")));
    const data = new FormData();
    data.append("file", file);
    xhr.send(data);
  });
}

// ------------------------------------------------------------------ upload
$("upload-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.file) uploadBill(state.file);
});

// replace = re-process the bill that already has this exact file
async function uploadBill(file, replace = false) {
  hide($("error"));
  hide($("duplicate"));
  hide($("result"));
  STEPS.forEach((step) => setStep(step, ""));
  setStep("upload", "active");
  show($("progress"));
  uploadBtn.disabled = true;
  $("progress-text").textContent = "Uploading…";

  let uploaded = false;
  const onUploaded = () => {
    if (uploaded) return;
    uploaded = true;
    setStep("upload", "done");
    setStep("ocr", "active");
    startClock("Reading the document…");
  };

  try {
    const url = replace ? "/api/bills/upload?replace=true" : "/api/bills/upload";
    const { status, body } = await postFile(
      url, file,
      (fraction) => { if (!uploaded) $("progress-text").textContent = `Uploading… ${Math.round(fraction * 100)}%`; },
      onUploaded,
    );
    onUploaded();  // small files may skip the upload "load" event
    stopClock();

    if (status === 409 && body) {
      hide($("progress"));
      showDuplicate(body, file);
      return;
    }
    if (status < 200 || status >= 300) {
      throw new Error((body && (body.message || body.detail)) || `Request failed (${status})`);
    }

    // The server runs OCR, extraction and validation in one request; reveal them in order.
    for (const step of ["ocr", "extract", "validate"]) {
      setStep(step, "active");
      await wait(160);
      setStep(step, "done");
    }
    await wait(350);
    hide($("progress"));  // the result and a toast take over from here
    if (body.saved === false) {
      showResult(body, true, file);
      toast("Invalid document: not saved", "error");
      return;
    }
    showResult(body, true);
    toast(replace ? `Bill #${body.id} re-processed` : `Bill #${body.id} extracted`);
    state.history.offset = 0;
    loadHistory();
  } catch (err) {
    stopClock();
    const current = STEPS.find((step) => document.querySelector(`#steps li[data-step="${step}"]`).className === "active");
    setStep(current || "upload", "failed");
    $("progress-text").textContent = "Processing failed.";
    showError(err.message);
  } finally {
    // Back to "drop your bill" once this file is done, unless another file was picked meanwhile
    if (state.file === file || state.file === null) resetDropzone();
    else uploadBtn.disabled = false;
  }
}

function showDuplicate(body, file) {
  $("duplicate-message").textContent = body.message;
  $("duplicate-view").onclick = () => { hide($("duplicate")); openBill(body.existing_bill_id); };
  $("duplicate-replace").onclick = () => uploadBill(file, true);
  show($("duplicate"));
}

// ------------------------------------------------------------------ result
async function openBill(id) {
  try {
    const response = await fetch(`/api/bills/${id}`);
    if (!response.ok) throw new Error((await readError(response)).message);
    hide($("progress"));
    showResult(await response.json(), true);
  } catch (err) {
    toast(err.message, "error");
  }
}

// localFile: the uploaded file when the result was not saved (its preview can't come from the server)
function showResult(bill, scroll = false, localFile = null) {
  state.bill = bill;
  state.page = 1;
  setLocalFile(bill.saved === false ? localFile : null);
  renderSummary(bill);
  renderData(bill);
  renderText(bill);
  renderJson(bill);
  selectTab("tab-data");
  show($("result"));
  loadPreview();
  history.replaceState(null, "", bill.id === null ? location.pathname + location.search : `#bill-${bill.id}`);
  markActiveRow();
  if (scroll) $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function setLocalFile(file) {
  if (state.localUrl) URL.revokeObjectURL(state.localUrl);
  state.localFile = file;
  state.localUrl = file ? URL.createObjectURL(file) : null;
}

function closeResult() {
  state.bill = null;
  setLocalFile(null);
  hide($("result"));
  history.replaceState(null, "", location.pathname + location.search);
  markActiveRow();
}

function renderSummary(bill) {
  const details = bill.validation_details || {};
  const badge = $("status-badge");
  badge.textContent = STATUS_LABELS[bill.validation_status] || bill.validation_status;
  badge.className = `badge badge-${bill.validation_status}`;

  const missing = (details.missing_fields || []).length;
  $("summary-fields").textContent = `${FIELDS.length - missing} of ${FIELDS.length} fields found`;

  const name = $("summary-name");
  name.textContent = bill.consumer_name || "Consumer name not found";
  name.classList.toggle("is-missing", !bill.consumer_name);

  const unsaved = bill.saved === false;
  $("not-saved").hidden = !unsaved;
  $("not-saved-reasons").replaceChildren(...(unsaved ? details.errors || [] : []).map((e) => el("li", {}, e)));

  $("result-meta").textContent = [
    unsaved ? "Not saved" : `Bill #${bill.id}`,
    bill.original_filename,
    METHOD_LABELS[bill.extraction_method] || bill.extraction_method,
    bill.page_count && `${bill.page_count} page${bill.page_count === 1 ? "" : "s"}`,
    bill.ocr_confidence !== null && bill.ocr_confidence !== undefined && `OCR confidence ${Math.round(bill.ocr_confidence)}%`,
    bill.processing_time_ms !== null && bill.processing_time_ms !== undefined && `${(bill.processing_time_ms / 1000).toFixed(1)} s`,
  ].filter(Boolean).join("  ·  ");

  const facts = [
    ["Account number", bill.account_number],
    ["Billing period", fmtPeriod(bill.billing_period)],
    ["Due date", fmtDate(bill.due_date)],
  ];
  $("summary-facts").replaceChildren(...facts.map(([label, value]) => el("div", {},
    el("dt", {}, label),
    el("dd", { class: value ? null : "is-missing" }, value || "Not found"),
  )));

  const amount = $("summary-amount");
  amount.textContent = fmtAmount(bill.net_amount_due) || "Not found";
  amount.classList.toggle("is-missing", bill.net_amount_due === null);
  $("summary-due").textContent = bill.due_date ? `Due ${fmtDate(bill.due_date)}` : "";
}

function statTile(label, value, unit, primary = false) {
  const formatted = fmtNumber(value);
  return el("div", { class: primary ? "stat primary" : "stat" },
    el("span", { class: "stat-label" }, label),
    formatted === null
      ? el("span", { class: "stat-value is-missing" }, "Not found")
      : el("span", { class: "stat-value" }, formatted, unit ? el("span", { class: "stat-unit" }, unit) : null),
  );
}

function renderData(bill) {
  const details = bill.validation_details || {};
  const derived = (bill.field_sources?.units_consumed || "").startsWith("derived:");

  const tiles = [
    statTile("Units consumed", bill.units_consumed, "kWh", true),
    statTile("Previous reading", bill.previous_reading),
    statTile("Current reading", bill.current_reading),
  ];
  if (details.multiplying_factor !== null && details.multiplying_factor !== undefined) {
    tiles.push(statTile("Multiplying factor", details.multiplying_factor));
  }
  $("stats").replaceChildren(...tiles);

  // Meter-reading check
  const meter = $("meter-check");
  const formula = details.calculation ? el("span", { class: "formula" }, details.calculation) : null;
  if (details.meter_reading_check === true) {
    meter.className = "meter-check ok";
    meter.replaceChildren(icon("check"), el("span", {}, "Readings match the units on the bill: ", formula));
  } else if (details.meter_reading_check === false) {
    meter.className = "meter-check mismatch";
    meter.replaceChildren(icon("alert"), el("span", {},
      `Readings don't match the units: calculated ${fmtNumber(details.calculated_units) ?? "—"}, `
      + `bill says ${fmtNumber(details.reported_units) ?? "—"}`, formula ? " · " : null, formula));
  } else if (derived && details.calculation) {
    meter.className = "meter-check";
    meter.replaceChildren(icon("calc"), el("span", {}, "Units aren't printed on the bill; calculated from the readings: ", formula));
  } else {
    meter.className = "meter-check";
    meter.replaceChildren(icon("info"), el("span", {}, "Meter check not possible: readings or units are missing."));
  }

  // Warnings, with links to possible duplicates. For an unsaved (INVALID) result the errors are
  // already in the "Not saved" box, and "X could not be extracted" for every field adds nothing.
  const unsaved = bill.saved === false;
  const nothingFound = (details.missing_fields || []).length === FIELDS.length;
  const warnings = unsaved ? (nothingFound ? [] : details.warnings || []) : bill.warnings || [];
  $("warnings-list").replaceChildren(...warnings.map((w) => el("li", {}, w)));
  warnings.length ? show($("warnings")) : hide($("warnings"));
  const duplicates = details.possible_duplicate_of || [];
  $("duplicate-links").replaceChildren(...duplicates.map((id) => {
    const link = el("button", { type: "button", class: "btn btn-secondary btn-sm" }, `View bill #${id}`);
    link.addEventListener("click", () => openBill(id));
    return link;
  }));
  $("duplicate-links").hidden = !duplicates.length;

  // Field table
  const checks = new Map((details.field_checks || []).map((c) => [c.field, c]));
  $("fields-body").replaceChildren(...FIELDS.map(([key, label]) => {
    const check = checks.get(key);
    const value = formatField(key, bill[key]);
    let valueCell;
    if (value === null) {
      valueCell = el("span", { class: "field-missing" }, icon("alert"), "Not found");
    } else if (check && (check.status === "mismatch" || check.status === "invalid")) {
      valueCell = el("span", { class: "field-value" },
        el("span", { class: `field-flag ${check.status}`, title: check.message || check.status }, icon("alert"), value),
        el("span", { class: "field-note" }, check.message || ""));
    } else {
      valueCell = el("span", { class: "field-value" }, value, key === "units_consumed" ? el("span", { class: "unit" }, "kWh") : null);
    }
    const source = describeSource(bill.field_sources?.[key]);
    return el("tr", {},
      el("td", {}, label),
      el("td", {}, valueCell),
      el("td", { class: "col-source" }, source ? el("span", { class: `chip ${source.cls}`, title: source.title }, source.text) : null),
    );
  }));

  const notes = details.notes || [];
  $("notes-list").replaceChildren(...notes.map((n) => el("li", {}, n)));
  notes.length ? show($("notes")) : hide($("notes"));
}

function formatField(key, value) {
  if (value === null || value === undefined || value === "") return null;
  if (key === "due_date") return fmtDate(value);
  if (key === "billing_period") return fmtPeriod(value);
  if (key === "net_amount_due") return fmtAmount(value);
  if (typeof value === "number") return fmtNumber(value);
  return String(value);
}

// "key_value:Consumer Name" -> Label “Consumer Name”; "derived:…" -> Calculated
function describeSource(source) {
  if (!source) return null;
  const i = source.indexOf(":");
  const kind = i < 0 ? source : source.slice(0, i);
  const detail = i < 0 ? "" : source.slice(i + 1);
  const quoted = detail ? ` “${detail}”` : "";
  if (kind === "key_value") return { text: `Label${quoted}`, cls: "", title: `Read next to the label${quoted}` };
  if (kind === "table") return { text: `Table${quoted}`, cls: "", title: `Read from the table column${quoted}` };
  if (kind === "inferred") return { text: "Inferred", cls: "chip-warn", title: "No label on the bill; identified from its format. Please verify." };
  if (kind === "derived") return { text: "Calculated", cls: "chip-accent", title: "Not printed on the bill; calculated from the meter readings" };
  const provider = PROVIDER_NAMES[kind] || kind.charAt(0).toUpperCase() + kind.slice(1);
  return { text: detail ? `${provider} · ${detail}` : provider, cls: "", title: `${provider} bill layout${detail ? `: ${detail}` : ""}` };
}

function renderText(bill) {
  const text = bill.raw_ocr_text || "";
  const lines = text ? text.split("\n") : [];
  $("raw-text").replaceChildren(...(lines.length
    ? lines.map((line) => el("span", { class: "line" }, line))
    : [el("span", { class: "line" }, "(no text)")]));
  $("text-meta").textContent = `${lines.length} lines · ${METHOD_LABELS[bill.extraction_method] || bill.extraction_method || "unknown"}`
    + (bill.ocr_confidence !== null && bill.ocr_confidence !== undefined ? ` · OCR confidence ${Math.round(bill.ocr_confidence)}%` : "");
}

function billJson(bill) {
  const details = bill.validation_details || {};
  return {
    id: bill.id,
    file: bill.original_filename,
    consumer_name: bill.consumer_name,
    account_number: bill.account_number,
    billing_period: bill.billing_period,
    billing_period_start: bill.billing_period_start,
    billing_period_end: bill.billing_period_end,
    due_date: bill.due_date,
    previous_reading: bill.previous_reading,
    current_reading: bill.current_reading,
    multiplying_factor: details.multiplying_factor ?? null,
    units_consumed: bill.units_consumed,
    net_amount_due: bill.net_amount_due,
    validation_status: bill.validation_status,
    meter_reading_check: details.meter_reading_check ?? null,
    warnings: bill.warnings || [],
    field_sources: bill.field_sources || {},
  };
}

// JSON with coloured keys/values; every token is added as text, never as HTML
function highlightJson(value) {
  const json = JSON.stringify(value, null, 2);
  const fragment = document.createDocumentFragment();
  const token = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;
  let last = 0;
  for (const m of json.matchAll(token)) {
    if (m.index > last) fragment.append(json.slice(last, m.index));
    if (m[1]) {
      fragment.append(el("span", { class: m[2] ? "j-key" : "j-str" }, m[1]));
      if (m[2]) fragment.append(m[2]);
    } else if (m[3]) {
      fragment.append(el("span", { class: m[3] === "null" ? "j-null" : "j-bool" }, m[3]));
    } else {
      fragment.append(el("span", { class: "j-num" }, m[0]));
    }
    last = m.index + m[0].length;
  }
  fragment.append(json.slice(last));
  return fragment;
}

function renderJson(bill) {
  $("json-view").replaceChildren(highlightJson(billJson(bill)));
}

$("copy-text").addEventListener("click", () => state.bill && copyText(state.bill.raw_ocr_text || "", "Text"));
$("copy-json").addEventListener("click", () => state.bill && copyText(JSON.stringify(billJson(state.bill), null, 2), "JSON"));
$("download-json").addEventListener("click", () => {
  if (!state.bill) return;
  const blob = new Blob([JSON.stringify(billJson(state.bill), null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: `bill-${state.bill.id ?? "unsaved"}.json` });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// -------------------------------------------------------------------- tabs
const TABS = ["tab-data", "tab-text", "tab-json"];

function selectTab(id) {
  for (const tabId of TABS) {
    const tab = $(tabId);
    const selected = tabId === id;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    $(tab.getAttribute("aria-controls")).hidden = !selected;
  }
}

for (const tabId of TABS) {
  $(tabId).addEventListener("click", () => selectTab(tabId));
  $(tabId).addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = TABS[(TABS.indexOf(tabId) + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
    selectTab(next);
    $(next).focus();
  });
}

// ----------------------------------------------------------------- preview
async function loadPreview() {
  const bill = state.bill;
  if (!bill) return;
  hide($("preview-frame"));
  $("preview-frame").removeAttribute("src");
  if (bill.saved === false) {
    showLocalPreview(bill);
    return;
  }
  const pages = Math.max(bill.page_count || 1, 1);
  const version = encodeURIComponent(bill.updated_at || "");
  $("pager").hidden = pages < 2;
  $("page-label").textContent = `${state.page} / ${pages}`;
  $("page-prev").disabled = state.page <= 1;
  $("page-next").disabled = state.page >= pages;
  $("open-original").href = `/api/bills/${bill.id}/file?v=${version}`;
  show($("open-original"));

  const img = $("preview-img");
  show($("preview-loading"));
  hide(img);
  hide($("preview-empty"));

  const request = ++state.previewRequest;
  try {
    const response = await fetch(`/api/bills/${bill.id}/preview?page=${state.page}&v=${version}`);
    if (request !== state.previewRequest) return;
    if (!response.ok) {
      const error = await readError(response);
      showPreviewEmpty(error.error === "file_not_available"
        ? "The original file is no longer stored on the server. The extracted data is still available."
        : error.message);
      if (error.error === "file_not_available") hide($("open-original"));
      return;
    }
    const blob = await response.blob();
    if (request !== state.previewRequest) return;
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = URL.createObjectURL(blob);
    img.src = state.previewUrl;
    img.alt = `Page ${state.page} of ${bill.original_filename}`;
    await img.decode().catch(() => {});
    if (request !== state.previewRequest) return;
    hide($("preview-loading"));
    show(img);
  } catch {
    if (request === state.previewRequest) showPreviewEmpty("The preview could not be loaded.");
  }
}

// A result that was not saved has no copy on the server: preview the file the user picked
function showLocalPreview(bill) {
  ++state.previewRequest;  // drop any server preview still loading
  $("pager").hidden = true;
  hide($("preview-loading"));
  hide($("preview-empty"));
  hide($("preview-img"));
  if (!state.localUrl) {
    hide($("open-original"));
    showPreviewEmpty("This document was not saved, so there is no stored copy to preview.");
    return;
  }
  $("open-original").href = state.localUrl;
  show($("open-original"));
  if (state.localFile.type === "application/pdf" || /\.pdf$/i.test(state.localFile.name)) {
    $("preview-frame").src = `${state.localUrl}#toolbar=0&view=FitH`;  // the browser's PDF viewer
    show($("preview-frame"));
  } else {
    const img = $("preview-img");
    img.src = state.localUrl;
    img.alt = `Preview of ${bill.original_filename}`;
    show(img);
  }
}

function showPreviewEmpty(message) {
  hide($("preview-loading"));
  hide($("preview-img"));
  hide($("preview-frame"));
  $("preview-empty-text").textContent = message;
  show($("preview-empty"));
}

$("page-prev").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadPreview(); } });
$("page-next").addEventListener("click", () => {
  if (state.bill && state.page < (state.bill.page_count || 1)) { state.page += 1; loadPreview(); }
});

// ----------------------------------------------------------------- history
async function loadHistory() {
  const h = state.history;
  const params = new URLSearchParams({ limit: HISTORY_PAGE_SIZE, offset: h.offset });
  if (h.status) params.set("validation_status", h.status);
  if (h.search) params.set("search", h.search);
  const request = ++h.request;
  const tbody = $("history-body");
  const table = tbody.closest("table");
  if (!tbody.children.length) tbody.replaceChildren(...skeletonRows(4));
  else table.classList.add("is-loading");

  try {
    const response = await fetch(`/api/bills?${params}`);
    if (!response.ok) throw new Error((await readError(response)).message);
    const data = await response.json();
    if (request !== h.request) return;
    if (!data.items.length && h.offset > 0) {  // the last row of a page was deleted
      h.offset = Math.max(0, h.offset - HISTORY_PAGE_SIZE);
      return loadHistory();
    }
    h.total = data.total;
    tbody.replaceChildren(...(data.items.length ? data.items.map(historyRow) : [emptyRow()]));
    renderHistoryFooter(data);
  } catch (err) {
    if (request !== h.request) return;
    tbody.replaceChildren(messageRow("Could not load bills", err.message));
    $("history-count").textContent = "";
    hide($("history-foot"));
  } finally {
    if (request === h.request) table.classList.remove("is-loading");
  }
}

function renderHistoryFooter(data) {
  const filtered = state.history.status || state.history.search;
  $("history-count").textContent = `${data.total} bill${data.total === 1 ? "" : "s"}${filtered ? " match" + (data.total === 1 ? "es" : "") + " your filters" : ""}`;
  const foot = $("history-foot");
  if (data.total <= HISTORY_PAGE_SIZE) { hide(foot); return; }
  show(foot);
  const from = data.offset + 1;
  const to = data.offset + data.items.length;
  $("page-info").textContent = `${from}–${to} of ${data.total}`;
  $("hist-prev").disabled = data.offset === 0;
  $("hist-next").disabled = to >= data.total;
}

function skeletonRows(count) {
  return Array.from({ length: count }, () => el("tr", {}, ...Array.from({ length: 9 }, (_, i) =>
    el("td", {}, i === 8 ? null : el("span", { class: "skeleton skeleton-line", style: `width:${[70, 90, 70, 60, 40, 50, 55, 45][i]}%` })))));
}

function messageRow(title, text, action) {
  return el("tr", {}, el("td", { colspan: 9, class: "empty-state" },
    el("p", { class: "empty-title" }, title),
    el("p", { class: "empty-text" }, text),
    action || null));
}

function emptyRow() {
  const h = state.history;
  if (!h.status && !h.search) return messageRow("No bills yet", "Upload a bill above and it will appear here.");
  const clear = el("button", { type: "button", class: "btn btn-secondary btn-sm" }, "Clear filters");
  clear.addEventListener("click", () => {
    h.search = "";
    h.status = "";
    h.offset = 0;
    $("history-search").value = "";
    setStatusFilter("");
    loadHistory();
  });
  return messageRow("No matching bills", "Try a different search or status filter.", clear);
}

function historyRow(b) {
  const period = fmtPeriod(b.billing_period, true);
  const tr = el("tr", { class: "row", tabindex: 0, "data-id": b.id },
    el("td", {}, el("div", { class: "bill-cell" },
      el("span", { class: "file-type small", "data-type": b.file_type === "jpeg" ? "jpg" : b.file_type }, fileTypeLabel(b.file_type)),
      el("span", { class: "bill-cell-text" },
        el("span", { class: "bill-id" }, `#${b.id}`),
        el("span", { class: "bill-file", title: b.original_filename }, b.original_filename)))),
    el("td", { class: b.consumer_name ? "cell-name" : "cell-muted", title: b.consumer_name || null }, b.consumer_name || "—"),
    el("td", { class: b.account_number ? null : "cell-muted" }, b.account_number || "—"),
    el("td", { class: period ? null : "cell-muted" }, period || "—"),
    el("td", { class: "num" }, fmtNumber(b.units_consumed) ?? el("span", { class: "cell-muted" }, "—")),
    el("td", { class: "num cell-strong" }, fmtAmount(b.net_amount_due) ?? el("span", { class: "cell-muted" }, "—")),
    el("td", {}, el("span", { class: `badge badge-${b.validation_status}` }, STATUS_LABELS[b.validation_status] || b.validation_status)),
    el("td", { class: "cell-time", title: new Date(b.created_at).toLocaleString("en-IN") }, relativeTime(b.created_at)),
  );
  const del = el("button", { type: "button", class: "icon-btn icon-btn-danger", "aria-label": `Delete bill #${b.id}`, title: "Delete" }, icon("trash"));
  del.addEventListener("click", (e) => { e.stopPropagation(); deleteBill(b); });
  tr.append(el("td", { class: "cell-action" }, del));

  tr.addEventListener("click", () => openBill(b.id));
  tr.addEventListener("keydown", (e) => {
    if (e.target === tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openBill(b.id); }
  });
  if (state.bill && state.bill.id === b.id) tr.classList.add("is-active");
  return tr;
}

function markActiveRow() {
  for (const row of document.querySelectorAll("#history-body tr.row")) {
    row.classList.toggle("is-active", Boolean(state.bill) && Number(row.dataset.id) === state.bill.id);
  }
}

async function deleteBill(b) {
  const label = b.consumer_name ? `bill #${b.id} (${b.consumer_name})` : `bill #${b.id}`;
  if (!confirm(`Delete ${label}? Its extracted data and stored file are removed permanently.`)) return;
  try {
    const response = await fetch(`/api/bills/${b.id}`, { method: "DELETE" });
    if (!response.ok) throw new Error((await readError(response)).message);
    if (state.bill && state.bill.id === b.id) closeResult();
    toast(`Bill #${b.id} deleted`);
    loadHistory();
  } catch (err) {
    toast(err.message, "error");
  }
}

function setStatusFilter(value) {
  for (const button of $("status-filter").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.status === value));
  }
}

$("status-filter").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-status]");
  if (!button) return;
  state.history.status = button.dataset.status;
  state.history.offset = 0;
  setStatusFilter(button.dataset.status);
  loadHistory();
});

let searchTimer = null;
$("history-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.history.search = e.target.value.trim();
    state.history.offset = 0;
    loadHistory();
  }, 300);
});

$("hist-prev").addEventListener("click", () => {
  state.history.offset = Math.max(0, state.history.offset - HISTORY_PAGE_SIZE);
  loadHistory();
});
$("hist-next").addEventListener("click", () => {
  state.history.offset += HISTORY_PAGE_SIZE;
  loadHistory();
});
$("refresh-btn").addEventListener("click", loadHistory);

// ------------------------------------------------------------------ health
async function loadHealth() {
  const node = $("health");
  try {
    const response = await fetch("/health");
    const h = await response.json();
    const ok = h.status === "ok";
    $("health-text").textContent = ok ? "Service online" : "Degraded";
    node.className = `health ${ok ? "ok" : "bad"}`;
    node.title = ok ? "Database and OCR are available" : `Database: ${h.database}, OCR: ${h.ocr}${h.ocr_detail ? ` (${h.ocr_detail})` : ""}`;
  } catch {
    $("health-text").textContent = "Offline";
    node.className = "health bad";
    node.title = "The server could not be reached";
  }
}

// -------------------------------------------------------------------- init
loadHealth();
loadHistory();
// Links like /#bill-12 open that bill, on load and when the address changes
function openLinkedBill() {
  const linked = /^#bill-(\d+)$/.exec(location.hash);
  if (linked && (!state.bill || state.bill.id !== Number(linked[1]))) openBill(Number(linked[1]));
}
window.addEventListener("hashchange", openLinkedBill);
openLinkedBill();
