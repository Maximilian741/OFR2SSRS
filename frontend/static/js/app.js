/* =========================================================
   Oracle2SSRS — frontend logic (clean rebuild)
   Vanilla JS. No IIFE, no fancy tricks. If anything breaks
   we want it visible in the console.
   ========================================================= */

console.log("[Oracle2SSRS] app.js loaded at", new Date().toLocaleTimeString());

// ----- State -----
const state = { data: null, activeTab: "mockup" };

// ----- DOM helpers -----
const $  = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const k of Object.keys(attrs)) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (v === true) node.setAttribute(k, "");
      else node.setAttribute(k, String(v));
    }
  }
  children.flat().forEach(c => {
    if (c == null || c === false) return;
    node.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  });
  return node;
}

// ----- Status / toast -----
function setStatus(text, kind) {
  const pill = $("#status-pill");
  if (!pill) return;
  pill.textContent = text;
  pill.className = "topbar-pill" + (kind ? " " + kind : "");
}
function toast(msg, kind) {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.className = "toast" + (kind ? " " + kind : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 3000);
}

// ----- Tabs -----
function activateTab(name) {
  state.activeTab = name;
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  // Tab panels only render content after a conversion has produced data.
  // Until then, hide every panel so the empty-state hero is the only thing
  // the user sees -- otherwise the mode toggle and the empty mockup-host
  // card show up on a fresh page load with nothing inside them.
  const hasData = !!state.data;
  $$(".tab-panel").forEach(p => {
    p.hidden = !hasData || (p.id !== "tab-" + name);
  });
  const empty = $("#empty-state");
  if (empty) empty.hidden = hasData;
  // Re-highlight Prism content if any in the active panel
  if (window.Prism) Prism.highlightAll();
  // Always reset tab-panels scroll to top so new content is visible
  const panels = $(".tab-panels");
  if (panels) panels.scrollTop = 0;
}



// Read the user-supplied connection string. Returned as-is (the backend
// XML-escapes it). Never logged.
function getConnString() {
  const el = document.getElementById("conn-string");
  return el ? (el.value || "").trim() : "";
}

// Shared data source PATH on the user's report server (e.g.
// "/Data Sources/MyOracle"). Baked into every generated RDL so uploads
// bind to the data source automatically. Not a secret -> persisted in
// localStorage so it survives reloads.
function getSharedDsPath() {
  const el = document.getElementById("shared-ds-path");
  return el ? (el.value || "").trim() : "";
}

function initSharedDsPath() {
  const el = document.getElementById("shared-ds-path");
  if (!el) return;
  try {
    const saved = localStorage.getItem("o2s_shared_ds_path");
    if (saved && !el.value) el.value = saved;
    el.addEventListener("change", function () {
      try { localStorage.setItem("o2s_shared_ds_path", (el.value || "").trim()); }
      catch (e) { /* private mode */ }
    });
  } catch (e) { /* private mode */ }
}

// SSRS report-server URL (for parameterized drill-through hyperlinks). A folder
// URL, not a secret -> persisted in localStorage so it survives reloads.
function getReportServerUrl() {
  const el = document.getElementById("report-server-url");
  return el ? (el.value || "").trim() : "";
}

function initReportServerUrl() {
  const el = document.getElementById("report-server-url");
  if (!el) return;
  try {
    const saved = localStorage.getItem("o2s_report_server_url");
    if (saved && !el.value) el.value = saved;
    el.addEventListener("change", function () {
      try { localStorage.setItem("o2s_report_server_url", (el.value || "").trim()); }
      catch (e) { /* private mode */ }
    });
  } catch (e) { /* private mode */ }
}

// Friendly DISPLAY label for the cover "generate all" sub-report link (e.g.
// "JV Standard 12 x 9 Envelope"). Not a secret -> persisted in localStorage.
function getGenerateAllLabel() {
  const el = document.getElementById("generate-all-label");
  return el ? (el.value || "").trim() : "";
}

function initGenerateAllLabel() {
  const el = document.getElementById("generate-all-label");
  if (!el) return;
  try {
    const saved = localStorage.getItem("o2s_generate_all_label");
    if (saved && !el.value) el.value = saved;
    el.addEventListener("change", function () {
      try { localStorage.setItem("o2s_generate_all_label", (el.value || "").trim()); }
      catch (e) { /* private mode */ }
    });
  } catch (e) { /* private mode */ }
}

// ----- App-level "How it works" modal -----
function _appHowtoHTML() {
  return (
    '<h2>How Oracle2SSRS works</h2>' +
    '<p class="howto-lead">Turn an Oracle Reports export into a deployable SSRS report in four steps. ' +
      'Two optional add-ons &mdash; <b>sub-reports</b> (drill-through links) and <b>bursting</b> (one report &rarr; ' +
      'many emails) &mdash; are explained below.</p>' +

    '<div class="howto-section">' +
      '<div class="howto-h">The basic flow</div>' +
      '<div class="howto-pipe">' +
        '<div class="howto-pipe-step"><span class="o2s-num">1</span>Drop artifacts<small>the Oracle <code>.xml</code>/<code>.rdf</code>, plus any <code>.sql</code>/<code>.docx</code>/images</small></div>' +
        '<div class="howto-pipe-arr">&rarr;</div>' +
        '<div class="howto-pipe-step"><span class="o2s-num">2</span>Convert<small>see the preview + the generated RDL, side by side</small></div>' +
        '<div class="howto-pipe-arr">&rarr;</div>' +
        '<div class="howto-pipe-step"><span class="o2s-num">3</span>Bind data source<small>point the report at your SSRS shared data source (sidebar)</small></div>' +
        '<div class="howto-pipe-arr">&rarr;</div>' +
        '<div class="howto-pipe-step"><span class="o2s-num">4</span>Download + deploy<small>upload the <code>.rdl</code> to your report server</small></div>' +
      '</div>' +
    '</div>' +

    '<div class="howto-section">' +
      '<div class="howto-h">Sub-reports (drill-through links)</div>' +
      '<p>One report can open <b>another</b> report for a clicked row. Each row carries its own values into the ' +
        'child, and a separate &ldquo;generate all&rdquo; link produces the whole set <b>in the same order</b>.</p>' +
      '<div class="o2s-flow">' +
        '<div class="o2s-node o2s-main"><div class="o2s-node-title">main report</div><div class="o2s-node-sub">lists every record</div></div>' +
        '<div class="o2s-links">' +
          '<div class="o2s-link"><span class="o2s-num">1</span><div>click <b>one record</b> <span class="o2s-arrow">&rarr;</span> just that one&rsquo;s child <em>(filtered)</em></div></div>' +
          '<div class="o2s-link"><span class="o2s-num">2</span><div><b>&ldquo;generate all&rdquo;</b> <span class="o2s-arrow">&rarr;</span> every child <em>(same order as the rows)</em></div></div>' +
        '</div>' +
        '<div class="o2s-node o2s-child"><div class="o2s-node-title">child report</div><div class="o2s-node-sub">e.g. one envelope</div></div>' +
      '</div>' +
      '<p>Build the child in the <b>Sub-reports</b> tab (drop its artifact), deploy the <b>child first</b>, then the ' +
        'main report &mdash; the links work in the SSRS viewer <b>and</b> in an exported PDF.</p>' +
    '</div>' +

    '<div class="howto-section">' +
      '<div class="howto-h">Bursting (one report &rarr; many emails)</div>' +
      '<p>Bursting splits <b>one</b> report run into <b>many PDFs &mdash; one per recipient</b> &mdash; and emails each ' +
        'automatically. Each recipient gets only their own page.</p>' +
      '<div class="o2s-flow">' +
        '<div class="o2s-node o2s-main"><div class="o2s-node-title">one run</div><div class="o2s-node-sub">many rows</div></div>' +
        '<div class="o2s-links">' +
          '<div class="o2s-link"><span class="o2s-num">1</span><div>split by recipient <span class="o2s-arrow">&rarr;</span> <b>one PDF each</b></div></div>' +
          '<div class="o2s-link"><span class="o2s-num">2</span><div>each PDF <span class="o2s-arrow">&rarr;</span> <b>emailed to that person</b> <em>(automatic)</em></div></div>' +
        '</div>' +
        '<div class="o2s-node o2s-child"><div class="o2s-node-title">per-recipient</div><div class="o2s-node-sub">PDF + email</div></div>' +
      '</div>' +
      '<p>Configure the recipient list + download the ready-to-run <b>Burst Pack</b> in the <b>Bursting</b> tab.</p>' +
    '</div>' +

    '<div class="burst-callout"><b>A worked example &mdash; MVWF_PERMIT:</b> the permit report lists every permittee. ' +
      'Each permit has a link to <b>that permittee&rsquo;s envelope</b> (JV_ENVELOPE_12), and a &ldquo;generate all&rdquo; ' +
      'link produces <b>every envelope in the same order as the permits</b> &mdash; so the two stacks match for mailing.</div>'
  );
}

function initHowto() {
  const modal = document.getElementById("howto-modal");
  const body = document.getElementById("howto-body");
  const openBtn = document.getElementById("howto-open");
  const closeBtn = document.getElementById("howto-close");
  const backdrop = document.getElementById("howto-backdrop");
  if (!modal || !body || !openBtn) return;
  const open = () => { body.innerHTML = _appHowtoHTML(); modal.hidden = false; };
  const close = () => { modal.hidden = true; };
  openBtn.addEventListener("click", open);
  if (closeBtn) closeBtn.addEventListener("click", close);
  if (backdrop) backdrop.addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.hidden) close(); });
}

// Append all deployment fields to a FormData (used by every convert call).
function appendDeployFields(fd) {
  const cs = getConnString(); if (cs) fd.append("connection_string", cs);
  const dsp = getSharedDsPath(); if (dsp) fd.append("shared_ds_path", dsp);
  const rsu = getReportServerUrl(); if (rsu) fd.append("report_server_url", rsu);
  const gal = getGenerateAllLabel(); if (gal) fd.append("generate_all_label", gal);
}

// ----- Report images (seals / logos / watermarks) -----
// The converter reports every layout image placeholder in
// data.image_slots. Slots whose bytes were embedded in the Oracle export
// show "embedded"; empty slots get a file input. Uploading re-converts
// server-side and refreshes the whole UI (RDL + mockup) in place.
function renderImageSlots(data) {
  const section = document.getElementById("report-images-section");
  const list = document.getElementById("image-slot-list");
  if (!section || !list) return;
  const slots = (data && data.image_slots) || [];
  if (!slots.length) { section.hidden = true; list.innerHTML = ""; return; }
  section.hidden = false;
  list.innerHTML = "";
  const mkRow = (label, slotKey, hasData) => {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:6px;margin:4px 0;";
    const name = document.createElement("span");
    name.textContent = label;
    name.style.cssText = "flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;";
    const status = document.createElement("span");
    status.textContent = hasData ? "✓" : "—";
    status.title = hasData ? "image present (from export or upload)" : "no image yet";
    status.style.cssText = "font-size:12px;color:" + (hasData ? "#15803d" : "#94a3b8") + ";";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.style.cssText = "max-width:150px;font-size:11px;";
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) uploadReportImage(slotKey, input.files[0]);
    });
    row.appendChild(name); row.appendChild(status); row.appendChild(input);
    return row;
  };
  slots.forEach(s => list.appendChild(mkRow(s.name, s.name, !!s.has_data)));
  if (slots.length > 1) {
    list.appendChild(mkRow("ALL placeholders", "*",
                           slots.every(s => !!s.has_data)));
  }
}

// ----- Batch migration (many reports -> RDLs + Migration Assessment) -----
function wireBatch() {
  const run = document.getElementById("batch-run");
  if (!run) return;
  run.addEventListener("click", async () => {
    const input = document.getElementById("batch-input");
    const status = document.getElementById("batch-status");
    const resBox = document.getElementById("batch-results");
    const files = (input && input.files) ? Array.from(input.files) : [];
    if (!files.length) {
      if (status) status.textContent = "Pick .xml files first.";
      return;
    }
    if (status) status.textContent = "Converting " + files.length + " report(s)…";
    if (resBox) resBox.innerHTML = "";
    const fd = new FormData();
    files.forEach(f => fd.append("files", f, f.name));
    appendDeployFields(fd);
    fd.append("target_db", getTargetDb());
    const chk = document.getElementById("batch-render");
    if (chk && chk.checked) fd.append("render", "1");
    try {
      const res = await fetch("/api/batch", { method: "POST", body: fd });
      const j = await res.json();
      if (!res.ok || j.error) throw new Error(j.error || "batch failed");
      const counts = {};
      (j.results || []).forEach(x => {
        counts[x.effort] = (counts[x.effort] || 0) + 1;
      });
      let msg = (j.results || []).length + " converted ("
        + Object.entries(counts).map(([k, v]) => v + " " + k).join(", ") + ")";
      if ((j.locked || []).length) {
        msg += " — " + j.locked.length + " skipped (over the "
          + (j.tier || "") + " batch limit)";
      }
      if (status) status.textContent = msg;
      if (resBox) {
        const a = document.createElement("a");
        a.href = "/api/download/batch-pack";
        a.textContent = "⬇ Download migration pack (RDLs + Assessment)";
        a.style.cssText = "display:block;margin:6px 0;font-size:12px;font-weight:600;";
        resBox.appendChild(a);
      }
      toast("Batch done — assessment ready", "ok");
    } catch (err) {
      if (status) status.textContent = "Failed: " + ((err && err.message) || err);
      toast((err && err.message) || "Batch failed", "err");
    }
  });
}

async function uploadReportImage(slot, file) {
  setStatus("Embedding image…", "busy");
  const fd = new FormData();
  fd.append("slot", slot);
  fd.append("image", file, file.name);
  try {
    const res = await fetch("/api/report-images/upload", { method: "POST", body: fd });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "image upload failed");
    if (json.rdl_xml) { onConverted(json); toast("Image embedded into the RDL + mockup", "ok"); }
    else { setStatus("Image stored", "ok"); toast(json.note || "Image stored", "ok"); }
  } catch (err) {
    console.error("[Oracle2SSRS] image upload failed:", err);
    setStatus("Error", "err");
    toast(err.message || "Image upload failed", "err");
  }
}

// Read the target-database toggle. Defaults to "oracle" so users who never
// touch the dropdown ship an RDL whose CommandText matches their Oracle
// backend rather than the translated T-SQL.
function getTargetDb() {
  const el = document.getElementById("target-db");
  const v = el ? (el.value || "").trim().toLowerCase() : "oracle";
  return (v === "sqlserver") ? "sqlserver" : "oracle";
}

// ----- API calls -----
async function uploadFile(file) {
  setStatus("Converting…", "busy");
  const fd = new FormData();
  fd.append("file", file);
  appendDeployFields(fd);
  fd.append("target_db", getTargetDb());
  try {
    const res = await fetch("/api/convert", { method: "POST", body: fd });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Conversion failed");
    onConverted(json);
  } catch (err) {
    console.error("[Oracle2SSRS] convert failed:", err);
    setStatus("Error", "err");
    toast(err.message || "Failed to convert", "err");
  }
}

async function uploadBundle(list) {
  setStatus("Ingesting " + list.length + " file(s)…", "busy");
  const fd = new FormData();
  list.forEach(f => fd.append("files", f, f._relPath || f.name));
  appendDeployFields(fd);
  fd.append("target_db", getTargetDb());
  try {
    const res = await fetch("/api/convert-bundle", { method: "POST", body: fd });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Bundle conversion failed");
    if (json.error === "no_convertible_artifacts") {
      setStatus("Nothing to convert", "err");
      renderIngestSummary(json.ingest_report || {});
      if (json.rdf_hint) {
        console.log("[Oracle2SSRS] .rdf detected:\n" + json.rdf_hint);
        toast(".rdf binary detected — run Oracle's rwconverter to export " +
              "XML first (exact command logged to the browser console)", "err");
      } else {
        toast("Found files but couldn't build a report — see Ingest Summary", "err");
      }
      return;
    }
    if (json.error) throw new Error(json.error);
    onConverted(json);
  } catch (err) {
    console.error("[Oracle2SSRS] bundle failed:", err);
    setStatus("Error", "err");
    toast(err.message || "Failed to ingest", "err");
  }
}

async function runSample(name, btn) {
  setStatus("Loading sample…", "busy");
  if (btn) btn.classList.add("busy");
  try {
    const _cs = getConnString();
    const _dsp = getSharedDsPath();
    const _td = getTargetDb();
    const _qsParts = [];
    if (_cs) _qsParts.push("connection_string=" + encodeURIComponent(_cs));
    if (_dsp) _qsParts.push("shared_ds_path=" + encodeURIComponent(_dsp));
    _qsParts.push("target_db=" + encodeURIComponent(_td));
    const _qs = _qsParts.length ? ("?" + _qsParts.join("&")) : "";
    const res = await fetch("/api/convert-sample/" + encodeURIComponent(name) + _qs, { method: "POST" });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Sample failed");
    onConverted(json);
  } catch (err) {
    console.error("[Oracle2SSRS] sample failed:", err);
    setStatus("Error", "err");
    toast(err.message || "Failed to load sample", "err");
  } finally {
    if (btn) btn.classList.remove("busy");
  }
}

// Decide which API to hit based on the file list.
function handleFileList(list) {
  if (!list || !list.length) return;
  if (list.length === 1) {
    const nm = (list[0].name || "").toLowerCase();
    if (nm.endsWith(".xml")) return uploadFile(list[0]);
  }
  uploadBundle(list);
}

// Recursively walk a webkitFileSystem entry.
function walkEntry(entry, prefix, out) {
  return new Promise(resolve => {
    if (!entry) return resolve();
    if (entry.isFile) {
      entry.file(f => {
        try { Object.defineProperty(f, "_relPath", { value: prefix + f.name }); } catch (e) {}
        out.push(f);
        resolve();
      }, () => resolve());
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const all = [];
      const readBatch = () => {
        reader.readEntries(async batch => {
          if (!batch.length) {
            for (const child of all) await walkEntry(child, prefix + entry.name + "/", out);
            resolve();
          } else { all.push.apply(all, batch); readBatch(); }
        }, () => resolve());
      };
      readBatch();
    } else { resolve(); }
  });
}

// ----- Conversion result handler -----
function onConverted(data) {
  // Dispatch a custom event so external listeners (e.g. the take-a-tour
  // walkthrough in demo_mode.js) can reliably detect conversion completion
  // without poking at the module-scoped `state`.
  try {
    document.dispatchEvent(new CustomEvent("o2s:converted", { detail: data }));
  } catch (e) {
    // CustomEvent unsupported is essentially impossible in any browser this
    // page supports, but log it loudly so we never silently swallow init
    // problems in this file again (the drop-zone regression hunt depended on
    // catch blocks not eating errors).
    console.error("[Oracle2SSRS] o2s:converted dispatch failed:", e);
  }
  state.data = data;
  setStatus("Converted", "ok");
  if ($("#empty-state")) $("#empty-state").hidden = true;
  renderSummary(data);
  renderImageSlots(data);
  if (data.ingest_report) renderIngestSummary(data.ingest_report);
  renderCrossValidation(data);
  renderEnrichmentBanner(data);
  renderMockupTab(data);
  renderRdlTab(data);
  renderSideBySideTab(data);
  renderLiveTab(data);
  renderValidationTab(data);
  renderDeploymentTab(data);
  renderExtrasTab(data);
  renderBurstingTab(data);
  renderSubreports(data);
  renderWarnings(data);
  renderPreflight(data);
  renderDeployStatus(data);
  if (window.Prism) Prism.highlightAll();
  showMockupCTA();
  pushRecent(data);
  // Show whatever tab was active
  activateTab(state.activeTab);
}

// ----- Sidebar summary card -----
function renderSummary(data) {
  const r = data.report || {};
  if ($("#sum-name"))     $("#sum-name").textContent     = r.name || "—";
  if ($("#sum-dtd"))      $("#sum-dtd").textContent      = r.dtd_version || "—";
  if ($("#sum-params"))   $("#sum-params").textContent   = (r.parameters || []).length;
  if ($("#sum-queries"))  $("#sum-queries").textContent  = (r.queries || []).length;
  if ($("#sum-formulas")) $("#sum-formulas").textContent = (r.formulas || []).length;
  const queryNotes = (r.queries || []).reduce((n, q) => n + (q.notes || []).length, 0);
  const totalWarn = (r.warnings || []).length + queryNotes;
  if ($("#sum-warnings")) $("#sum-warnings").textContent = totalWarn;

  // Fidelity score (source -> RDL coverage). 1.0 = every column + param kept.
  const fid = data.fidelity_report;
  const frow = $("#sum-fidelity-row");
  const fval = $("#sum-fidelity");
  if (fval && fid && typeof fid.score === "number") {
    const pct = Math.round(fid.score * 100);
    fval.textContent = pct + "%";
    if (frow) {
      frow.classList.remove("fidelity-full", "fidelity-partial");
      frow.classList.add(fid.score >= 1 ? "fidelity-full" : "fidelity-partial");
      if (fid.summary) frow.title = fid.summary;
    }
  } else if (fval) {
    fval.textContent = "—";
  }

  if ($("#summary-section")) $("#summary-section").hidden = false;
}

// ----- Ingest summary panel (above tabs) -----


// ----- Cross-validation panel (inside the ingest summary) -----
function renderCrossValidation(data) {
  const xv = data && data.cross_validation;
  const wrap = document.getElementById("ingest-summary");
  if (!xv || !wrap) return;

  // Remove a stale render
  const old = document.getElementById("xv-block");
  if (old) old.remove();

  const block = document.createElement("div");
  block.id = "xv-block";
  block.className = "xv-block";

  const sum = xv.summary || {};
  const total = (sum.error||0) + (sum.warning||0) + (sum.info||0);
  let cls = "xv-clean";
  if ((sum.error||0) > 0) cls = "xv-error";
  else if ((sum.warning||0) > 0) cls = "xv-warn";

  block.innerHTML =
    '<div class="xv-head ' + cls + '">' +
      '<b>Cross-validation</b> — ' +
      'XML parser vs supporting artifacts. ' +
      '<span class="xv-counts">' +
        (sum.error   ? '<span class="xv-count xv-e">' + sum.error   + ' errors</span>'   : '') +
        (sum.warning ? '<span class="xv-count xv-w">' + sum.warning + ' warnings</span>' : '') +
        (sum.info    ? '<span class="xv-count xv-i">' + sum.info    + ' info</span>'     : '') +
        (total === 0 ? '<span class="xv-count xv-ok">all clean</span>' : '') +
      '</span>' +
    '</div>';

  // Per-section detail
  const sections = [
    ["sql_doc",     "SQL doc"],
    ["pdf",         "Rendered PDF"],
    ["screenshots", "Screenshots"],
  ];
  sections.forEach(([key, label]) => {
    const sec = xv[key] || {};
    if (!sec.checked && (!sec.findings || !sec.findings.length)) return;
    const card = document.createElement("details");
    card.className = "xv-section";
    if ((sec.findings || []).some(f => f.severity !== "info")) card.open = true;
    const stats = sec.stats || {};
    const statBits = Object.keys(stats).map(k =>
      '<code>' + k + ':' + (typeof stats[k] === "object" ? JSON.stringify(stats[k]) : stats[k]) + '</code>'
    ).join(" &nbsp; ");
    let html = '<summary><b>' + label + '</b> ' +
               (sec.checked ? '' : '<span class="xv-skip">(not checked)</span>') +
               '</summary>';
    if (statBits) html += '<div class="xv-stats">' + statBits + '</div>';
    if (sec.findings && sec.findings.length) {
      html += '<ul class="xv-findings">';
      sec.findings.forEach(f => {
        html += '<li class="xv-' + (f.severity || "info") + '">' +
                '<span class="xv-sev">' + (f.severity||"") + '</span>' +
                '<code class="xv-rule">' + (f.rule||"") + '</code>' +
                ' <span class="xv-subj">' + (f.subject ? '(' + f.subject + ')' : '') + '</span>' +
                '<div class="xv-msg">' + (f.message || "") + '</div>' +
                '</li>';
      });
      html += '</ul>';
    }
    card.innerHTML = html;
    block.appendChild(card);
  });

  wrap.appendChild(block);
  wrap.hidden = false;
}


function renderIngestSummary(report) {
  const wrap = $("#ingest-summary");
  if (!wrap) return;
  const banner = $("#ingest-banner");
  const totals = $("#ingest-totals");
  const body   = $("#ingest-body");
  if (banner) banner.hidden = true;
  if (totals) totals.innerHTML = "";
  if (body)   body.innerHTML   = "";

  const summary = report.category_summary || [];
  const totalsDict = report.totals || {};
  if (totals) {
    Object.keys(totalsDict).forEach(k => {
      if (totalsDict[k] === 0) return;
      const cls = "ingest-badge " + ({ xml: "xml", sql: "sql", screenshots: "img", screenshot: "img", docs: "docx", rdf: "rdf", unknown: "unknown" }[k] || "unknown");
      totals.appendChild(el("span", { class: cls, text: `${totalsDict[k]} ${k}` }));
    });
  }
  if (body && summary.length) {
    summary.forEach(c => {
      body.appendChild(el("div", { class: "ingest-row" },
        el("span", { class: "ingest-badge " + (c.category || "unknown") , text: c.category || "?" }),
        el("span", { class: "ingest-file", text: c.file || "" }),
        el("span", { class: "ingest-note", text: c.note || `confidence ${c.confidence ?? "?"}` })
      ));
    });
  }
  wrap.hidden = false;
}

// ----- Tab 1: Mockup -----
// Two view modes per conversion:
//   "frontend" -> data.mockup_html (filled with sample data; what SSRS will render)
//   "backend"  -> data.mockup_backend_html (placeholders; Report Builder design view)
// The toggle buttons live in #tab-mockup and set state.mockupMode.
function renderMockupTab(data) {
  const host = $("#mockup-host");
  if (!host) return;
  // state is the module-level closure variable declared at the top of this
  // file (const state = {...}). DON'T reference window.state — it doesn't
  // exist and the toggle would silently no-op.
  const mode = state.mockupMode || "frontend";
  const html = mode === "backend"
    ? (data.mockup_backend_html || data.mockup_html || "<em>No backend skeleton.</em>")
    : (data.mockup_html || "<em>No mockup available.</em>");
  host.innerHTML = html;
}

function _setMockupMode(mode) {
  state.mockupMode = mode;
  const fe = document.getElementById("mockup-mode-frontend");
  const be = document.getElementById("mockup-mode-backend");
  // The active/inactive look is now driven entirely by CSS rules keyed off
  // aria-checked. We keep the legacy .mockup-mode-active class in sync for
  // any older selectors, but don't write inline styles anymore.
  if (fe && be) {
    const feOn = mode === "frontend";
    const beOn = mode === "backend";
    fe.setAttribute("aria-checked", feOn ? "true" : "false");
    be.setAttribute("aria-checked", beOn ? "true" : "false");
    fe.classList.toggle("mockup-mode-active", feOn);
    be.classList.toggle("mockup-mode-active", beOn);
    fe.tabIndex = feOn ? 0 : -1;
    be.tabIndex = beOn ? 0 : -1;
  }
  if (state.data) renderMockupTab(state.data);
}

// Wire toggle buttons once on load. We attach immediately if the DOM is
// already ready, otherwise wait for DOMContentLoaded — app.js may be
// included near the end of <body>, in which case the listener never fires.
function _wireMockupToggle() {
  const fe = document.getElementById("mockup-mode-frontend");
  const be = document.getElementById("mockup-mode-backend");
  if (fe && !fe._wired) {
    fe.addEventListener("click", () => _setMockupMode("frontend"));
    fe._wired = true;
  }
  if (be && !be._wired) {
    be.addEventListener("click", () => _setMockupMode("backend"));
    be._wired = true;
  }
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _wireMockupToggle);
} else {
  _wireMockupToggle();
}

// ----- Tab 2: RDL XML -----
function renderRdlTab(data) {
  const code = $("#rdl-code");
  if (code) code.textContent = data.rdl_xml || "";
}

// ----- Tab 3: Side-by-side -----
function renderSideBySideTab(data) {
  const lc = $("#oracle-code");
  const rc = $("#rdl-code-2");
  if (lc) lc.textContent = data.oracle_xml || "";
  if (rc) rc.textContent = data.rdl_xml || "";
}

// ----- Tab 4: Live data -----
function renderLiveTab(data) {
  const host = $("#live-host");
  if (!host) return;
  host.innerHTML = "";
  const r = data.report || {};
  const queries = r.queries || [];
  const params  = r.parameters || [];
  if (!queries.length) {
    host.appendChild(el("div", { class: "results-empty", text: "No queries in this report." }));
    return;
  }
  queries.forEach((q, idx) => {
    const card = el("div", { class: "query-card" });
    const head = el("div", { class: "query-head" },
      el("div", { class: "query-name", text: q.name || ("Query " + (idx + 1)) }),
      el("button", { class: "btn btn-primary", onClick: () => runQuery(card, q) }, "Run query")
    );
    card.appendChild(head);
    if (q.notes && q.notes.length) {
      const wl = el("div", { class: "warning-list" });
      q.notes.forEach(n => wl.appendChild(el("span", { class: "warn-chip", text: n })));
      card.appendChild(wl);
    }
    if (params.length) {
      const form = el("div", { class: "param-form" });
      params.forEach(p => {
        const id = "p_" + (q.name || idx) + "_" + p.name;
        form.appendChild(el("label", { for: id },
          el("span", {}, p.label || p.name),
          el("input", { id, "data-name": p.name, type: "text",
            value: p.initial_value != null ? p.initial_value : "",
            placeholder: p.input_mask || "" })
        ));
      });
      card.appendChild(form);
    }
    card.appendChild(el("pre", { class: "query-tsql", text: q.tsql || q.sql || "(empty)" }));
    card.appendChild(el("div", { class: "results-host" }));
    host.appendChild(card);
  });
}
async function runQuery(card, q) {
  const inputs = $$(".param-form input", card);
  const parameters = {};
  inputs.forEach(i => { parameters[i.dataset.name] = i.value; });
  const resultsHost = $(".results-host", card);
  resultsHost.innerHTML = '<div class="results-empty">Running…</div>';
  try {
    const res = await fetch("/api/run-query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql: q.tsql || q.sql, parameters })
    });
    const json = await res.json();
    if (!res.ok || json.error) throw new Error(json.error || "Query failed");
    renderResults(resultsHost, json);
  } catch (err) {
    resultsHost.innerHTML = "";
    resultsHost.appendChild(el("div", { class: "results-empty", text: "Error: " + (err.message || err) }));
  }
}
function renderResults(host, json) {
  host.innerHTML = "";
  const warnings = json.warnings || [];
  if (warnings.length) {
    const wl = el("div", { class: "warning-list" });
    warnings.forEach(w => wl.appendChild(el("span", { class: "warn-chip", text: w })));
    host.appendChild(wl);
  }
  const cols = json.columns || [];
  const rows = json.rows || [];
  if (!rows.length) {
    host.appendChild(el("div", { class: "results-empty", text: "No rows returned." }));
    return;
  }
  const wrap = el("div", { class: "results-table-wrap" });
  const tbl  = el("table", { class: "results-table" });
  const thead = el("thead", {}, el("tr", {}, ...cols.map(c => el("th", { text: c }))));
  const tbody = el("tbody");
  rows.forEach(r => {
    const tr = el("tr");
    cols.forEach(c => tr.appendChild(el("td", { text: r && r[c] != null ? String(r[c]) : "" })));
    tbody.appendChild(tr);
  });
  tbl.appendChild(thead); tbl.appendChild(tbody); wrap.appendChild(tbl); host.appendChild(wrap);
}

// ----- Deploy-status strip: one-glance "converted + verdict + next step" -----
function renderDeployStatus(data) {
  const host = document.getElementById("deploy-status");
  if (!host) return;
  const issues = (data.preflight || {}).issues || [];
  const n = s => issues.filter(i => (i.severity || "").toUpperCase() === s).length;
  const blockers = n("BLOCKER"), reds = n("RED");
  const name = (data.report && data.report.name) || "Report";
  let cls, verdict, glyph;
  if (blockers) { cls = "ds-blocker"; glyph = "!"; verdict = blockers + " blocker" + (blockers > 1 ? "s" : "") + " to fix before upload"; }
  else if (reds) { cls = "ds-warn"; glyph = "!"; verdict = reds + " runtime issue" + (reds > 1 ? "s" : "") + " — see Validation"; }
  else { cls = "ds-ready"; glyph = "✓"; verdict = "Ready to deploy"; }
  host.className = "deploy-status " + cls;
  host.innerHTML =
    '<div class="ds-left">' +
      '<span class="ds-glyph">' + glyph + '</span>' +
      '<span class="ds-name">' + escapeHtml(name) + '</span>' +
      '<span class="ds-verdict">' + verdict + '</span>' +
    '</div>' +
    '<div class="ds-steps"><b>1</b> Download .rdl <span>→</span> <b>2</b> Upload to SSRS ' +
      (getSharedDsPath()
        ? '<span>→</span> <b>3</b> Run (data source pre-bound to ' + escapeHtml(getSharedDsPath()) + ')</div>'
        : '<span>→</span> <b>3</b> Repoint data source + Refresh Fields <span>→</span> <b>4</b> Run</div>') +
    '<button id="ds-download" class="ds-btn" type="button">Download .rdl</button>';

  // Drill-through dependency: the report links to child report(s) that MUST be
  // built + deployed too, or the link is dead. Surface it loudly (you asked for
  // a prompt to add the extra piece).
  const links = data.subreport_links || [];
  if (links.length) {
    const names = links.map(l => l.child_name).filter(Boolean);
    const uniq = names.filter((v, i) => names.indexOf(v) === i);
    const note = document.createElement("div");
    note.className = "ds-drillnote";
    note.innerHTML = "⤷ <b>Drills through to:</b> " +
      uniq.map(escapeHtml).join(", ") +
      " — build " + (uniq.length > 1 ? "these child reports" : "this child report") +
      " in the <b>Sub-Reports</b> tab, then deploy " + (uniq.length > 1 ? "them" : "it") +
      " to SSRS <b>before the parent</b> (child-first — the link is resolved by name at " +
      "click time). The Sub-Reports tab has the full step-by-step.";
    host.appendChild(note);
  }
  host.hidden = false;
  const btn = document.getElementById("ds-download");
  const dl = document.getElementById("download-rdl");
  if (btn && dl) btn.addEventListener("click", () => dl.click());
}

// ----- Upload-safety preflight: sidebar verdict banner + Validation detail -----
// The authoritative "will it upload + refresh + run in Report Builder?" check.
// Surfaced BEFORE download so a blocker (e.g. a parameter that would prompt on
// refresh, or a dangling field reference) is caught here, not in Report Builder.
function renderPreflight(data) {
  const pf = data.preflight || {};
  const issues = pf.issues || [];
  const count = s => issues.filter(i => (i.severity || "").toUpperCase() === s).length;
  const blockers = count("BLOCKER"), reds = count("RED"), ambers = count("AMBER");

  const banner = document.getElementById("preflight-banner");
  if (banner) {
    let cls, label, sub;
    if (pf.source_kind) {
      // Partial Oracle artifact (customization overlay / data-model-only /
      // layout fragment) — be honest: it's not a full report.
      cls = "pf-red";
      const nice = { customization_overlay: "Customization overlay",
                     data_model_only: "Data-model-only export",
                     layout_fragment: "Layout fragment" }[pf.source_kind]
                   || "Partial artifact";
      label = "ⓘ " + nice + " — not a full report";
      sub = pf.source_kind_message || "Provide the complete report XML.";
      banner.className = "preflight-banner " + cls;
      banner.innerHTML = "<b>" + label + "</b><span>" + sub + "</span>";
      banner.hidden = false;
      return;
    }
    if (blockers) {
      cls = "pf-blocker";
      label = "⚠ " + blockers + " blocker" + (blockers > 1 ? "s" : "");
      sub = "Will not upload / refresh cleanly — fix before deploying.";
    } else if (reds) {
      cls = "pf-red";
      label = "⚠ " + reds + " runtime issue" + (reds > 1 ? "s" : "");
      sub = "Uploads, but may error at run time — see Validation.";
    } else {
      cls = "pf-ready";
      label = "✓ Upload-ready";
      sub = ambers ? (ambers + " note" + (ambers > 1 ? "s" : "") + " — optional review.")
                   : "No blockers detected.";
    }
    banner.className = "preflight-banner " + cls;
    banner.innerHTML = "<b>" + label + "</b><span>" + sub + "</span>";
    banner.hidden = false;
  }

  // Validation-tab detail: prepend an ordered preflight issue list.
  const vhost = document.getElementById("validation-host");
  if (vhost && issues.length) {
    const sec = document.createElement("div");
    sec.className = "preflight-detail";
    let html = "<h4>Upload-safety preflight</h4>";
    ["BLOCKER", "RED", "AMBER"].forEach(sev => {
      issues.filter(i => (i.severity || "").toUpperCase() === sev).forEach(i => {
        html += "<div class='pf-issue pf-" + sev.toLowerCase() + "'>" +
                "<span class='pf-sev'>" + sev + "</span>" +
                "<code class='pf-rule'>" + escapeHtml(i.rule || "") + "</code>" +
                "<span class='pf-msg'>" + escapeHtml(i.message || "") + "</span></div>";
      });
    });
    sec.innerHTML = html;
    vhost.insertBefore(sec, vhost.firstChild);
  }

  // Validation tab chip = the upload-blocking issues (most important signal).
  const v = data.validation_issues || [];
  const vSerious = v.filter(i => i.severity === "error" || i.severity === "warning").length;
  setBadge("badge-validate", blockers + reds + vSerious);
}

// ----- Tab 5: Validation (advanced) -----
function renderValidationTab(data) {
  const host = $("#validation-host");
  if (!host) return;
  host.innerHTML = "";
  const issues = data.validation_issues || [];
  const summary = $("#validation-summary");
  if (summary) {
    summary.innerHTML = "";
    const counts = { error: 0, warning: 0, info: 0 };
    issues.forEach(i => { counts[i.severity || "info"]++; });
    Object.keys(counts).forEach(k => {
      if (counts[k] === 0) return;
      summary.appendChild(el("span", { class: "sev-chip sev-" + k, text: counts[k] + " " + k }));
    });
  }
  if (!issues.length) {
    host.appendChild(el("div", { class: "results-empty", text: "No validation issues." }));
    return;
  }
  ["error", "warning", "info"].forEach(sev => {
    const matching = issues.filter(i => (i.severity || "info") === sev);
    if (!matching.length) return;
    host.appendChild(el("h4", { class: "sev-head sev-" + sev, text: sev.toUpperCase() + " (" + matching.length + ")" }));
    matching.forEach(i => {
      const card = el("div", { class: "issue-card" });
      card.appendChild(el("div", { class: "issue-meta" },
        el("span", { class: "issue-rule", text: i.rule || "?" }),
        el("span", { class: "issue-scope", text: " @ " + (i.scope || "report") }),
        i.line ? el("span", { class: "issue-loc", text: " L" + i.line + (i.col ? ":" + i.col : "") }) : null
      ));
      card.appendChild(el("div", { class: "issue-msg", text: i.message || "" }));
      if (i.excerpt) card.appendChild(el("pre", { class: "issue-excerpt", text: i.excerpt }));
      host.appendChild(card);
    });
  });
}

// ----- Tab 6: Deploy checklist -----
function renderDeploymentTab(data) {
  const host = $("#deploy-host");
  if (!host) return;
  host.innerHTML = "";
  const checklist = data.deployment_checklist || [];
  if (!checklist.length) {
    host.appendChild(el("div", { class: "results-empty", text: "No deployment checklist generated." }));
    return;
  }
  checklist.forEach(step => {
    const status = step.status || "todo";
    const icon = ({ auto: "✔", todo: "🛠", manual: "○", caution: "⚠" })[status] || "○";
    const card = el("details", { class: "deploy-step deploy-" + status, open: step.step <= 3 });
    card.appendChild(el("summary", {},
      el("span", { class: "deploy-icon", text: icon }),
      el("span", { class: "deploy-num", text: " " + step.step + ". " }),
      el("span", { class: "deploy-title", text: step.title || "" }),
      el("span", { class: "deploy-status-chip", text: status })
    ));
    const body = el("div", { class: "deploy-body" });
    body.innerHTML = renderMd(step.body_md || "");
    card.appendChild(body);
    host.appendChild(card);
  });
}
function renderMd(md) {
  // Tiny markdown: paragraphs, bold, code, fenced blocks, bullet lists
  if (!md) return "";
  const escape = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inlineMd = s => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const blocks = md.split(/\n{2,}/);
  const out = [];
  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith("```")) {
      const code = trimmed.replace(/^```\w*\n?/, "").replace(/```$/, "");
      out.push("<pre><code>" + escape(code) + "</code></pre>");
    } else if (/^[-*]\s/.test(trimmed)) {
      const items = trimmed.split("\n").map(l => "<li>" + inlineMd(escape(l.replace(/^[-*]\s+/, ""))) + "</li>");
      out.push("<ul>" + items.join("") + "</ul>");
    } else if (/^\d+\.\s/.test(trimmed)) {
      const items = trimmed.split("\n").map(l => "<li>" + inlineMd(escape(l.replace(/^\d+\.\s+/, ""))) + "</li>");
      out.push("<ol>" + items.join("") + "</ol>");
    } else {
      out.push("<p>" + inlineMd(escape(trimmed)).replace(/\n/g, "<br>") + "</p>");
    }
  }
  return out.join("\n");
}

// ----- Warnings footer + tab badges -----
function renderWarnings(data) {
  const r = data.report || {};
  const items = [];
  (r.warnings || []).forEach(w => items.push({ scope: "report", text: w }));
  (r.queries || []).forEach(q => (q.notes || []).forEach(n => items.push({ scope: q.name, text: n })));

  // Conversion-warnings footer removed to declutter the bottom of the screen.
  // Warnings still surface via the per-tab badges (below) and the sidebar
  // summary’s Warnings row.

  setBadge("badge-mockup", (r.warnings || []).length);
  setBadge("badge-rdl",    (r.warnings || []).length);
  setBadge("badge-side",   (r.warnings || []).length);
  setBadge("badge-live",   (r.queries || []).reduce((n, q) => n + (q.notes || []).length, 0));

  const issues = data.validation_issues || [];
  const errCount  = issues.filter(i => i.severity === "error").length;
  const warnCount = issues.filter(i => i.severity === "warning").length;
  setBadge("badge-validate", errCount + warnCount);

  const checklist = data.deployment_checklist || [];
  const todoCount = checklist.filter(s => s.status === "todo" || s.status === "caution").length;
  setBadge("badge-deploy", todoCount);
}
function setBadge(id, n) {
  const node = document.getElementById(id);
  if (!node) return;
  if (n > 0) { node.textContent = String(n); node.hidden = false; }
  else { node.hidden = true; node.textContent = ""; }
}


// Conversion-fidelity headline card: what the converter preserved from the
// source vs what still needs manual wiring. The honest counterpart to the
// upload-safety preflight -- nothing is silently dropped.
function renderFidelityCard(host, fid) {
  if (!fid || typeof fid.score !== "number") return;
  const cats = fid.categories || {};
  const pct = Math.round(fid.score * 100);
  const full = fid.score >= 1;
  const section = document.createElement("section");
  section.className = "extras-section extras-compact fidelity-card " +
    (full ? "fidelity-full" : "fidelity-partial");

  const C = cats;
  const stat = [];
  if (C.columns)   stat.push(["Columns",    C.columns.preserved   + " / " + C.columns.total]);
  if (C.parameters)stat.push(["Parameters", C.parameters.preserved + " / " + C.parameters.total]);
  if (C.layout_fields) stat.push(["Layout fields", C.layout_fields.bound + " / " + C.layout_fields.total]);
  if (C.formulas && C.formulas.total) stat.push(["Formulas (need wiring)", String(C.formulas.total)]);
  if (C.summaries) stat.push(["Summary aggregates", String(C.summaries.aggregates_in_rdl || 0)]);

  let html =
    "<h3>Conversion fidelity " +
      "<span class='fidelity-score'>" + pct + "%</span></h3>" +
    "<div class='fidelity-bar'><span style='width:" + pct + "%'></span></div>" +
    "<p class='fidelity-blurb'>" +
      (full
        ? "Faithful 1:1 copy &mdash; every source column and parameter is preserved in the RDL."
        : "Some source columns or parameters are not bound in the RDL (see below).") +
      " <span class='fidelity-note'>Score = columns + parameters preserved. " +
      "Formula bodies and any items listed below still need manual wiring at deploy time.</span>" +
    "</p>";

  html += "<div class='fidelity-stats'>";
  stat.forEach(([k, v]) => {
    html += "<div class='fidelity-stat'><span>" + escapeHtml(k) + "</span><b>" + escapeHtml(v) + "</b></div>";
  });
  html += "</div>";

  const needs = fid.needs_attention || [];
  if (needs.length) {
    html += "<details class='fidelity-needs'" + (full ? "" : " open") + ">" +
      "<summary>" + needs.length + " item(s) to wire / check</summary><ul>";
    needs.forEach(n => { html += "<li>" + escapeHtml(n) + "</li>"; });
    html += "</ul></details>";
  }
  section.innerHTML = html;
  host.appendChild(section);
}

// ----- Tab 7: Extras (audit trail, AI prompts, bursting / DDS) - advanced -----
function renderExtrasTab(data) {
  const host = document.getElementById("extras-host");
  if (!host) return;
  host.innerHTML = "";

  // Headline card: Conversion fidelity (source -> RDL coverage self-check)
  renderFidelityCard(host, data.fidelity_report);

  // Compact card: Bursting / DDS summary
  const burst = (data && data.bursting) || {};
  const burstSection = document.createElement("section");
  burstSection.className = "extras-section extras-compact";
  const burstTitle = burst.is_bursting ? "Bursting / DDS detected" : "Bursting / DDS not detected";
  burstSection.innerHTML = "<h3>" + burstTitle + "</h3>";
  if (burst.is_bursting) {
    burstSection.innerHTML +=
      "<div class='extras-meta'>" +
      "<b>Burst key:</b> " + escapeHtml(burst.burst_key_field || "?") +
      " &middot; <b>Filename:</b> " + escapeHtml(burst.filename_pattern || "?") +
      "</div>";
    if (burst.burst_query) {
      burstSection.innerHTML +=
        "<details><summary>Burst query (T-SQL) <button class='btn btn-ghost btn-copy' data-copy='burst_query'>Copy</button></summary>" +
        "<pre class='code-block'><code class='language-sql'>" + escapeHtml(burst.burst_query) + "</code></pre></details>";
    }
    if (burst.powershell_script) {
      burstSection.innerHTML +=
        "<details><summary>PowerShell DDS emulator <button class='btn btn-ghost btn-copy' data-copy='powershell_script'>Copy</button></summary>" +
        "<pre class='code-block'><code>" + escapeHtml(burst.powershell_script) + "</code></pre></details>";
    }
  }
  host.appendChild(burstSection);

  // Compact card: AI-assist prompts
  const prompts = data.ai_prompts || [];
  const promptSection = document.createElement("section");
  promptSection.className = "extras-section extras-compact";
  promptSection.innerHTML = "<h3>AI-assist prompts (" + prompts.length + ")</h3>";
  prompts.forEach((p, idx) => {
    const card = document.createElement("details");
    card.className = "extras-prompt diff-" + (p.difficulty || "medium");
    card.innerHTML =
      "<summary>" +
      "<span class='extras-tag'>" + escapeHtml(p.scope || "") + "</span> " +
      "<b>" + escapeHtml(p.name || ("prompt #" + (idx+1))) + "</b>" +
      " <span class='extras-difficulty'>" + escapeHtml(p.difficulty || "medium") + "</span>" +
      " <button class='btn btn-ghost btn-copy' data-copy-prompt='" + idx + "'>Copy</button>" +
      "</summary>" +
      "<pre class='code-block'><code>" + escapeHtml(p.prompt_template || "") + "</code></pre>";
    promptSection.appendChild(card);
  });
  host.appendChild(promptSection);

  // Compact card: Audit trail
  const trail = data.audit_trail || [];
  const auditSection = document.createElement("section");
  auditSection.className = "extras-section extras-compact";
  auditSection.innerHTML = "<h3>Audit trail (" + trail.length + ")</h3>";
  if (trail.length) {
    let table = "<div class='extras-table-wrap'><table class='extras-table'><thead><tr>" +
      "<th>#</th><th>Stage</th><th>Scope</th><th>Rule</th><th>Before</th><th>After</th>" +
      "</tr></thead><tbody>";
    trail.forEach(e => {
      table += "<tr>" +
        "<td>" + escapeHtml(String(e.step || "")) + "</td>" +
        "<td>" + escapeHtml(e.stage || "") + "</td>" +
        "<td>" + escapeHtml(e.scope || "") + "</td>" +
        "<td><code>" + escapeHtml(e.rule || "") + "</code></td>" +
        "<td><code class='audit-snippet'>" + escapeHtml(e.before || "") + "</code></td>" +
        "<td><code class='audit-snippet'>" + escapeHtml(e.after || "") + "</code></td>" +
        "</tr>";
    });
    table += "</tbody></table></div>";
    auditSection.innerHTML += table;
  }
  host.appendChild(auditSection);

  // Wire up Copy buttons
  host.querySelectorAll(".btn-copy").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      const key = btn.dataset.copy;
      const promptIdx = btn.dataset.copyPrompt;
      let text = "";
      if (key && data.bursting && data.bursting[key]) text = data.bursting[key];
      else if (promptIdx != null && data.ai_prompts && data.ai_prompts[+promptIdx]) text = data.ai_prompts[+promptIdx].prompt_template || "";
      if (!text) return;
      navigator.clipboard.writeText(text).then(
        () => toast("Copied", "ok"),
        () => toast("Copy failed", "err")
      );
    });
  });

  // Set badge: count items needing attention (= AI prompts > 0 OR bursting detected)
  const extrasCount = prompts.length + (burst.is_bursting ? 1 : 0);
  setBadge("badge-extras", extrasCount);
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}


// ============================================================
// Wire everything up after DOM is ready
// ============================================================


// ----- Phase 3: simplified UI wiring -----
function wireSimplifiedUI() {
  const adv = document.getElementById("advanced-toggle");
  if (adv) {
    adv.addEventListener("click", () => {
      document.body.classList.toggle("show-advanced");
      adv.textContent = document.body.classList.contains("show-advanced")
        ? "Simple view"
        : "Advanced views";
    });
  }
  const cta = document.getElementById("cta-download-rdl");
  if (cta) {
    cta.addEventListener("click", () => {
      if (!state.data) { toast("Convert a report first", "err"); return; }
      window.location.href = "/api/download/rdl";
    });
  }
}
// Show the CTA bar after a successful conversion
function showMockupCTA() {
  const cta = document.getElementById("mockup-cta");
  if (cta) cta.hidden = false;
}



// ----- Tab: Bursting / Email distribution -----
function renderBurstingTab(data) {
  const host = document.getElementById("burst-host");
  if (!host) return;
  host.innerHTML = "";

  const burst = (data && data.bursting) || {};

  // Plug-and-play: show + hydrate the Distribution Settings form when
  // bursting was detected. The form lives in the static HTML (above
  // #burst-host); we just hydrate values and wire its buttons once.
  hydrateBurstForm(data, burst);

  // ---- Header: plain-English detection ----
  const header = document.createElement("div");
  header.className = "burst-header " + (burst.is_bursting ? "burst-yes" : "burst-no");
  if (burst.is_bursting) {
    header.innerHTML =
      '<div class="burst-h-icon">📨</div><div>' +
      '<div class="burst-h-title">Per-recipient distribution detected</div>' +
      '<div class="burst-h-meta">This report sent a separate PDF to each ' +
      '<code>' + escHtml(burst.burst_key_field || "recipient") + '</code>. ' +
      'SSRS Standard can\'t do that on its own — the <b>Burst Pack</b> does it for you.</div></div>';
  } else {
    header.innerHTML =
      '<div class="burst-h-icon">○</div><div>' +
      '<div class="burst-h-title">No bursting detected</div>' +
      '<div class="burst-h-meta">This report runs once per execution, not once per recipient.</div></div>';
  }
  host.appendChild(header);

  if (!burst.is_bursting) { setBadge("badge-burst", 0); return; }

  const keyc = escHtml(burst.burst_key_field || "recipient");

  // ---- Turnkey "do exactly this" guide ----
  const brname = (data && data.report && data.report.name) || "your report";
  const guide = document.createElement("section");
  guide.className = "burst-section burst-guide o2s-howto";
  guide.innerHTML =
    '<h3>What bursting does</h3>' +
    '<div class="burst-meta">Bursting takes <b>one</b> report run and splits it into <b>many PDFs &mdash; one per ' +
      'recipient</b> &mdash; then emails each to the right person automatically. Each <code>' + keyc +
      '</code> gets only their own page.</div>' +
    '<div class="o2s-flow">' +
      '<div class="o2s-node o2s-main"><div class="o2s-node-title">' + escHtml(brname) + '</div>' +
        '<div class="o2s-node-sub">one run &middot; many rows</div></div>' +
      '<div class="o2s-links">' +
        '<div class="o2s-link"><span class="o2s-num">1</span><div>split by <code>' + keyc + '</code> ' +
          '<span class="o2s-arrow">&rarr;</span> <b>one PDF each</b></div></div>' +
        '<div class="o2s-link"><span class="o2s-num">2</span><div>each PDF <span class="o2s-arrow">&rarr;</span> ' +
          '<b>emailed to that recipient</b> <em>(automatic)</em></div></div>' +
      '</div>' +
      '<div class="o2s-node o2s-child"><div class="o2s-node-title">per-recipient PDF</div>' +
        '<div class="o2s-node-sub">+ its own email</div></div>' +
    '</div>' +
    '<div class="burst-files-sub" style="margin-top:14px">Make it run &mdash; 4 steps</div>' +
    '<ol class="burst-steps">' +
      '<li>In <b>Distribution Settings</b> above: set your <b>email server &amp; sender</b>, and edit ' +
        '<b>Email-Source SQL</b> so it returns <b>one row per recipient</b> (their email + their ' +
        '<code>' + keyc + '</code>). We pre-filled a working template — point it at your DB.</li>' +
      '<li>Click <b>Download Burst Pack</b> → one <code>.zip</code>. <b>Unzip it on your SSRS server.</b></li>' +
      '<li>Do the <b>one-time host setup</b> in <code>service-account-setup.md</code> (a service account ' +
        '+ SMTP rights). You do this <b>once, ever</b> — then it covers every report.</li>' +
      '<li><b>Run <code>Send-Reports.ps1</code></b> (right-click → Run with PowerShell) to send yourself a ' +
        'test, then schedule it in Task Scheduler. Done — each recipient gets their own PDF.</li>' +
    '</ol>' +
    '<div class="burst-callout"><b>You fill in one thing:</b> the recipient list — the <b>Email-Source SQL</b> ' +
      'above (who gets a copy + their email). SMTP, PDF rendering, the per-recipient loop, retries, and ' +
      '"don\'t send twice" are all done for you. Full walkthrough is in <code>README.md</code> in the pack.</div>';
  host.appendChild(guide);

  // ---- Collapsed: the generated files (inspect/copy if you want) ----
  const files = document.createElement("details");
  files.className = "burst-section burst-files";
  files.innerHTML = '<summary>Peek inside the pack — generated files</summary>';
  const addBlock = (k, title, hint, lang) => {
    if (!burst[k]) return;
    const d = document.createElement("details");
    d.className = "burst-block";
    d.innerHTML =
      '<summary><b>' + title + '</b> ' +
      '<button class="btn btn-ghost btn-copy" data-copy="' + k + '">Copy</button></summary>' +
      '<pre class="code-block"><code' + (lang ? ' class="language-' + lang + '"' : '') + '>' +
      escHtml(burst[k]) + '</code></pre>' +
      (hint ? '<div class="burst-hint">' + hint + '</div>' : '');
    files.appendChild(d);
  };
  addBlock("email_burst_query", "recipient SQL", "This is the one query to replace with your real recipient + email list.", "sql");
  addBlock("email_powershell_script", "Send-Reports.ps1", "The driver. Runs as your service account; reads burst.config.json at run time.", "");
  addBlock("email_config_template", "burst.config.json", "Your settings — pre-filled from Distribution Settings above.", "");
  const checklist = burst.service_account_checklist || [];
  if (checklist.length) {
    const wrap = document.createElement("div");
    wrap.className = "burst-block";
    wrap.innerHTML = '<div class="burst-files-sub">service-account-setup.md — one-time host setup</div>';
    const ol = document.createElement("ol");
    ol.className = "burst-checklist";
    checklist.forEach(s => {
      const li = document.createElement("li");
      li.innerHTML = '<div class="burst-step-title">' + escHtml(s.title || "") + '</div>' +
                     '<div class="burst-step-body">' + (s.body || "") + '</div>';
      ol.appendChild(li);
    });
    wrap.appendChild(ol);
    files.appendChild(wrap);
  }
  host.appendChild(files);

  // Wire up Copy buttons
  host.querySelectorAll(".btn-copy").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      const key = btn.dataset.copy;
      const text = (data.bursting && data.bursting[key]) || "";
      if (!text) return;
      navigator.clipboard.writeText(text).then(
        () => toast("Copied " + key, "ok"),
        () => toast("Copy failed", "err")
      );
    });
  });

  // Set badge to 1 if bursting was detected (draws the eye)
  setBadge("badge-burst", burst.is_bursting ? 1 : 0);
}


// ===========================================================================
// Sub-Reports: detection-driven + manual, with a live preview (mockup + RDL)
// ===========================================================================
//
// A child report can be built from ANY artifact -- its Oracle XML, an existing
// .rdl, or its SQL (.sql/.docx/.txt). The backend routes the artifacts through
// the SAME pipeline the main report uses, so the child gets a full RDL plus an
// HTML mockup we render exactly like the first-page preview.

// Turn a raw report name into readable link text: "JV_ENVELOPE_12" ->
// "JV Envelope 12". Short all-caps tokens (JV) stay as acronyms; digits stay.
// The exact wording (e.g. "Standard 12 x 9") isn't in the source, so this is a
// sensible STARTING point the user edits -- never blank, never the ugly raw name.
function _humanizeReportName(name) {
  return String(name || "").split(/[_\s]+/).filter(Boolean).map(t => {
    if (/^\d+$/.test(t)) return t;
    if (/^[A-Z0-9]{1,3}$/.test(t)) return t;       // acronym (JV, US, ID)
    return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
  }).join(" ");
}

// Pre-fill the "Cover hyperlink text" box from the detected child on the fly,
// so it's never blank. Only fills an EMPTY box (never clobbers the user's own
// text or a remembered value).
function _prefillGenerateAllLabel(children) {
  const el = document.getElementById("generate-all-label");
  if (!el || (el.value || "").trim()) return;
  let saved = "";
  try { saved = localStorage.getItem("o2s_generate_all_label") || ""; } catch (e) {}
  if (saved) { el.value = saved; return; }
  const first = (children || []).find(c => c.detected && c.name);
  if (first) el.value = _humanizeReportName(first.name);
}

function renderSubreports(data) {
  // Reset per-conversion state (a new parent => fresh children + previews).
  state.subreportBuilds = {};
  state.subreportChildren = _subCollectDetected(data);
  _prefillGenerateAllLabel(state.subreportChildren);

  const tabBtn = document.getElementById("tabbtn-subreports");
  // The tab is available after any conversion so a sub-report can be added
  // manually even when no drill-through link was auto-detected.
  if (tabBtn) tabBtn.hidden = !state.data;
  setBadge("badge-subreports", state.subreportChildren.filter(c => c.detected).length);

  renderSubreportsTab();
  renderSubreportSidebar();
}

function _subCollectDetected(data) {
  const links = (data && data.subreport_links) || [];
  const out = [];
  const seen = new Set();
  links.forEach((ln, i) => {
    const name = (ln.child_name || ("Child_" + (i + 1))).trim();
    if (seen.has(name)) return;
    seen.add(name);
    out.push({
      name: name, detected: true, link: ln,
      artifacts: (ln.artifacts || []).map(a => (a && a.name) ? a.name : a),
    });
  });
  return out;
}

function _subFindChild(name) {
  return (state.subreportChildren || []).find(c => c.name === name) || null;
}

// ---- Sidebar: compact drop slots that appear under the main drop zone ----
function renderSubreportSidebar() {
  const section = document.getElementById("subreport-section");
  const slots = document.getElementById("subreport-slots");
  const hint = document.getElementById("subreport-hint");
  if (!section || !slots) return;
  if (!state.data) { section.hidden = true; return; }
  section.hidden = false;

  const children = state.subreportChildren || [];
  const detected = children.filter(c => c.detected).length;
  if (hint) {
    hint.textContent = detected
      ? ("Detected " + detected + " drill-through link" + (detected === 1 ? "" : "s") +
         ". Drop the child's artifacts to generate it.")
      : "No drill-through detected. Use + Add to build a sub-report from any artifact.";
  }

  slots.innerHTML = "";
  children.forEach(c => slots.appendChild(_subBuildSidebarSlot(c)));
}

function _subBuildSidebarSlot(c) {
  const wrap = el("div", { class: "subreport-slot", "data-child": c.name });
  const dz = el("div", {
      class: "subreport-mini-drop", tabindex: 0, role: "button",
      "aria-label": "Drop artifacts for " + c.name,
    },
    el("div", { class: "subreport-mini-name", text: c.name }),
    el("div", { class: "subreport-mini-sub", text: "drop XML · .rdl · SQL, or click" }),
    (c.artifacts && c.artifacts.length)
      ? el("div", { class: "subreport-mini-have", text: c.artifacts.length + " artifact(s)" })
      : null
  );
  const input = el("input", { type: "file", multiple: true, hidden: true });
  const msg = el("div", { class: "subreport-mini-msg" });
  const fire = (files) => { if (files && files.length) subUploadArtifacts(c.name, files, msg); };

  dz.addEventListener("click", () => input.click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  input.addEventListener("change", () => { fire(Array.from(input.files || [])); input.value = ""; });
  ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, (e) => {
    e.preventDefault(); e.stopPropagation(); dz.classList.add("drag");
  }));
  ["dragleave", "dragend"].forEach(ev => dz.addEventListener(ev, (e) => {
    e.preventDefault(); e.stopPropagation(); dz.classList.remove("drag");
  }));
  dz.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); dz.classList.remove("drag");
    fire(Array.from((e.dataTransfer && e.dataTransfer.files) || []));
  });

  wrap.appendChild(dz);
  wrap.appendChild(input);
  wrap.appendChild(msg);
  return wrap;
}

// ---- The "chicken-and-egg", answered in the tool ----------------------------
// A drill-through parent references its child BY NAME. SSRS resolves that name
// only when the link is CLICKED (runtime), never at upload -- so the child must
// already be on the server, i.e. deploy CHILD FIRST. And because the parent's
// link may reference a guessed child name, building the child here re-syncs the
// parent, so you re-download the parent after (main -> children -> main again).
// This guide spells out both orders. Reuses the bursting tab's guide styles.
function _subDeployGuideHTML(children) {
  const names = (children || []).filter(c => c.detected).map(c => c.name);
  const params = [];
  (children || []).forEach(c => {
    const bp = (c.link && c.link.bind_params) || [];
    bp.forEach(p => { if (p && params.indexOf(p) < 0) params.push(p); });
  });
  const childList = names.length
    ? names.map(n => '<code>' + escHtml(n) + '</code>').join(", ")
    : "the child report";
  const paramNote = params.length
    ? '<div class="burst-callout"><b>The parent passes these to the child:</b> ' +
        params.map(p => '<code>' + escHtml(p) + '</code>').join(" ") +
        '. The tool already declared them <b>hidden</b> in the child, so running the ' +
        'child on its own never prompts for them.</div>'
    : '';
  const parentName = (state.data && state.data.report && state.data.report.name) || "your main report";
  const childName = names[0] || "the child report";
  const paramChips = params.length
    ? params.map(p => '<code>' + escHtml(p) + '</code>').join(" ")
    : "";
  return (
    '<section class="burst-section burst-guide o2s-howto">' +
      '<h3>How sub-report (drill-through) links work</h3>' +
      '<div class="burst-meta">Your <b>main report</b> (<code>' + escHtml(parentName) + '</code>) lists many ' +
        'records. A <b>drill-through link</b> lets each row open a <b>second report</b> (' + childList + ') for ' +
        '<i>that</i> record. A separate <b>“generate all”</b> link produces the whole set <b>in the same order</b> ' +
        'as the rows you just ran.</div>' +
      // ---- visual flow diagram ----
      '<div class="o2s-flow">' +
        '<div class="o2s-node o2s-main"><div class="o2s-node-title">' + escHtml(parentName) + '</div>' +
          '<div class="o2s-node-sub">main report · every record</div></div>' +
        '<div class="o2s-links">' +
          '<div class="o2s-link"><span class="o2s-num">1</span><div>Click <b>one record’s</b> link ' +
            '<span class="o2s-arrow">&rarr;</span> opens <b>just that one’s</b> child <em>(filtered to it)</em></div></div>' +
          '<div class="o2s-link"><span class="o2s-num">2</span><div>Click <b>“generate all”</b> ' +
            '<span class="o2s-arrow">&rarr;</span> <b>every</b> child <em>(same order as the rows)</em></div></div>' +
        '</div>' +
        '<div class="o2s-node o2s-child"><div class="o2s-node-title">' + escHtml(childName) + '</div>' +
          '<div class="o2s-node-sub">the child report(s)</div></div>' +
      '</div>' +
      (paramChips
        ? '<div class="burst-callout"><b>What gets passed:</b> the parent hands the child ' + paramChips +
            ' for the clicked row, so the child shows exactly that record. The links fire in the SSRS viewer ' +
            '<b>and</b> in an exported PDF.</div>'
        : '<div class="burst-callout">The links fire in the SSRS viewer <b>and</b> in an exported PDF.</div>') +
      // ---- collapsible step-by-step ----
      '<details class="o2s-details"><summary>Show me the exact steps (build &rarr; deploy &rarr; test)</summary>' +
        '<div class="burst-files-sub" style="margin-top:10px">1 · BUILD HERE (in this tool)</div>' +
        '<ol class="burst-steps">' +
          '<li><b>Main report first</b> — already converted. The tool read its link(s) and listed each child below.</li>' +
          '<li><b>Build each child</b> — drop the child’s artifact (Oracle <code>.xml</code>, a <code>.rdl</code>, ' +
            'or <code>.sql</code>/<code>.docx</code>) in its slot. The tool builds the child and declares the exact ' +
            'parameters the parent forwards.</li>' +
          '<li><b>Re-download the main</b> — if a child’s real name differs from the link’s guess the tool ' +
            '<b>auto-re-syncs the parent</b> (“Parent re-synced”). Re-download the main <code>.rdl</code> from the ' +
            '<b>RDL</b> tab + each child from its card. <i>(“main &rarr; children &rarr; main again.”)</i></li>' +
        '</ol>' +
        '<div class="burst-files-sub" style="margin-top:12px">2 · DEPLOY TO SSRS (child first)</div>' +
        '<ol class="burst-steps">' +
          '<li><b>Upload the child(ren) first</b> — into the <b>same SSRS folder</b> as the parent, under the ' +
            '<b>exact name</b> on each card (SSRS resolves the link by that bare name, at click time).</li>' +
          '<li><b>Upload the main report second.</b></li>' +
          '<li><b>Test</b> — open the main in SSRS, click <b>one record’s</b> link (its child) and the ' +
            '<b>“generate all”</b> link (the ordered set).</li>' +
        '</ol>' +
        paramNote +
      '</details>' +
    '</section>'
  );
}

// ---- Tab: full cards with artifacts, actions, and a live preview ----
function renderSubreportsTab() {
  const host = document.getElementById("subreports-host");
  if (!host) return;
  const children = state.subreportChildren || [];
  const guide = _subDeployGuideHTML(children);
  if (!children.length) {
    host.innerHTML = guide +
      '<div class="subreport-empty-state">No sub-reports yet. Drop a child ' +
      "report’s artifacts in the <b>Sub-reports</b> box in the sidebar, " +
      "or click <b>+ Add</b> there to build one from any artifact.</div>";
    return;
  }
  host.innerHTML = guide + children.map((c, i) => _subCardHTML(c, i)).join("");
  _subWireCards(host);
}

function _subCardHTML(c, idx) {
  const child = escHtml(c.name);
  const ln = c.link || {};
  const meta = [];
  if (c.detected) {
    if (ln.link_text)    meta.push("Link text: <b>" + escHtml(ln.link_text) + "</b>");
    if (ln.parent_field) meta.push("Parent field: <code>" + escHtml(ln.parent_field) + "</code>");
    if (ln.url_formula)  meta.push("URL formula: <code>" + escHtml(ln.url_formula) + "</code>");
    const binds = (ln.bind_params || []).map(escHtml).join(", ");
    if (binds) meta.push("Forwarded params: <code>" + binds + "</code>");
  } else {
    meta.push('<span class="subreport-empty">added manually</span>');
  }
  const arts = (c.artifacts || []).map(a => '<span class="chip">' + escHtml(a) + "</span>").join(" ")
    || '<span class="subreport-empty">no artifacts yet</span>';

  const built = (state.subreportBuilds || {})[c.name];
  let preview = "";
  if (built) {
    const srcLabel = ({ oracle_xml: "Oracle XML", rdl: "an existing .rdl",
                        sql: "SQL", stub: "a placeholder" })[built.source] || built.source;
    const issues = (built.issues || []).map(i => "<li>" + escHtml(i) + "</li>").join("");
    const nFields = (built.fields || []).length;
    preview =
      '<div class="subreport-preview">' +
        '<div class="subreport-preview-bar">' +
          '<span class="subreport-built-tag">Built from ' + escHtml(srcLabel) + "</span>" +
          '<span class="subreport-built-meta">' + nFields + " field" + (nFields === 1 ? "" : "s") +
            (built.report_name ? " · " + escHtml(built.report_name) : "") + "</span>" +
          '<button type="button" class="btn btn-primary subreport-dl" data-child="' + child +
            '">Download .rdl</button>' +
        "</div>" +
        '<div class="subreport-mock">' + (built.mockup_html || "") + "</div>" +
        (issues
          ? '<details class="subreport-issues"><summary>Notes (' + (built.issues || []).length +
            ")</summary><ul>" + issues + "</ul></details>"
          : "") +
        '<details class="subreport-rdl"><summary>Generated RDL XML</summary>' +
          '<pre class="code-block"><code class="language-xml">' +
          escHtml(built.rdl_xml || "") + "</code></pre></details>" +
      "</div>";
  }

  return (
    '<div class="subreport-card" data-child="' + child + '">' +
      '<div class="subreport-head">' +
        '<h3 class="subreport-title">Child report: ' + child + "</h3>" +
        '<span class="subreport-num">' + (c.detected ? "link #" + (idx + 1) : "manual") + "</span>" +
      "</div>" +
      '<div class="subreport-meta">' + meta.join(" &middot; ") + "</div>" +
      '<div class="subreport-artifacts">Artifacts: ' + arts + "</div>" +
      '<div class="subreport-label-row">' +
        '<label class="subreport-label-lbl">Display name / size' +
          '<input type="text" class="conn-input subreport-label" data-child="' + child +
          '" placeholder="e.g. JV Standard 12 x 9 Envelope" autocomplete="off" spellcheck="false">' +
        "</label>" +
        '<div class="muted-note">Shown as the cover link text on the parent report. ' +
          'An envelope with a size (e.g. <code>12 x 9</code>) is built at that page size. ' +
          'Remembered in this browser.</div>' +
      "</div>" +
      '<div class="subreport-actions">' +
        '<label class="btn btn-ghost subreport-add">Add artifact(s)' +
          '<input type="file" multiple class="subreport-upload" data-child="' + child +
          '" hidden></label>' +
        '<button type="button" class="btn btn-ghost subreport-clear" data-child="' + child +
          '">Clear</button>' +
        '<button type="button" class="btn btn-primary subreport-build" data-child="' + child +
          '">Build &amp; preview</button>' +
        '<span class="subreport-msg" data-child="' + child + '"></span>' +
      "</div>" +
      preview +
    "</div>"
  );
}

function _subWireCards(host) {
  host.querySelectorAll(".subreport-upload").forEach(input => {
    input.addEventListener("change", () => {
      const child = input.dataset.child;
      const msg = host.querySelector('.subreport-msg[data-child="' + child + '"]');
      if (input.files && input.files.length) subUploadArtifacts(child, Array.from(input.files), msg);
      input.value = "";
    });
  });
  host.querySelectorAll(".subreport-clear").forEach(btn => {
    btn.addEventListener("click", async () => {
      const child = btn.dataset.child;
      const msg = host.querySelector('.subreport-msg[data-child="' + child + '"]');
      if (msg) msg.textContent = "Clearing…";
      try {
        await fetch("/api/subreport/" + encodeURIComponent(child) + "/clear", { method: "POST" });
        const c = _subFindChild(child); if (c) c.artifacts = [];
        if (state.subreportBuilds) delete state.subreportBuilds[child];
        renderSubreportsTab();
        renderSubreportSidebar();
      } catch (err) { if (msg) msg.textContent = "Clear failed: " + err; }
    });
  });
  host.querySelectorAll(".subreport-label").forEach(input => {
    const child = input.dataset.child;
    try {
      const saved = localStorage.getItem("o2s_sublabel_" + child);
      if (saved && !input.value) input.value = saved;
    } catch (e) { /* private mode */ }
    input.addEventListener("change", () => {
      try { localStorage.setItem("o2s_sublabel_" + child, (input.value || "").trim()); }
      catch (e) { /* private mode */ }
    });
  });
  host.querySelectorAll(".subreport-build").forEach(btn => {
    btn.addEventListener("click", () => {
      const child = btn.dataset.child;
      const msg = host.querySelector('.subreport-msg[data-child="' + child + '"]');
      subBuildAndPreview(child, { msgEl: msg });
    });
  });
  host.querySelectorAll(".subreport-dl").forEach(btn => {
    btn.addEventListener("click", () => {
      const child = btn.dataset.child;
      window.location.href = "/api/subreport/" + encodeURIComponent(child) + "/download";
    });
  });
  if (window.Prism) Prism.highlightAll();
}

// ---- Shared upload + build flow (used by sidebar slots and tab cards) ----
async function subUploadArtifacts(child, files, msgEl) {
  const setMsg = (t) => { if (msgEl) msgEl.textContent = t; };
  try {
    setMsg("Uploading " + files.length + " file(s)…");
    const fd = new FormData();
    files.forEach(f => fd.append("artifact", f, f.name));
    const r = await fetch("/api/subreport/" + encodeURIComponent(child) + "/upload",
                          { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || "upload failed");
    const c = _subFindChild(child);
    if (c) c.artifacts = j.artifacts || [];
    renderSubreportSidebar();
    setMsg("Building…");
    await subBuildAndPreview(child, { activate: true, msgEl: msgEl });
  } catch (err) {
    setMsg("Failed: " + ((err && err.message) || err));
  }
}

// The per-child display label (cover link text + envelope size). Read from the
// card input if present, else the remembered value. Persisted both per-child
// AND as the global generate-all label so the PARENT cover link uses it.
function _subLabel(child) {
  const el = document.querySelector('.subreport-label[data-child="' + cssAttr(child) + '"]');
  let v = el ? (el.value || "").trim() : "";
  if (!v) { try { v = localStorage.getItem("o2s_sublabel_" + child) || ""; } catch (e) {} }
  return v;
}
function cssAttr(s) { return String(s).replace(/"/g, '\\"'); }

async function subBuildAndPreview(child, opts) {
  opts = opts || {};
  const setMsg = (t) => { if (opts.msgEl) opts.msgEl.textContent = t; };
  setMsg("Building…");
  const label = _subLabel(child);
  if (label) {
    try {
      localStorage.setItem("o2s_sublabel_" + child, label);
      localStorage.setItem("o2s_generate_all_label", label);
    } catch (e) { /* private mode */ }
    const sidebar = document.getElementById("generate-all-label");
    if (sidebar && !sidebar.value) sidebar.value = label;
  }
  try {
    const r = await fetch("/api/subreport/" + encodeURIComponent(child) + "/build",
                          { method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ shared_ds_path: getSharedDsPath(),
                                                   display_label: label }) });
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || "build failed");
    state.subreportBuilds = state.subreportBuilds || {};
    state.subreportBuilds[child] = j;
    const c = _subFindChild(child);
    if (c && j.artifacts) c.artifacts = j.artifacts;
    const tabBtn = document.getElementById("tabbtn-subreports");
    if (tabBtn) tabBtn.hidden = false;
    renderSubreportsTab();
    if (opts.activate) activateTab("subreports");
    // Chicken-and-egg killer: when the child's actual report name differs
    // from what the parent's drill-through referenced, the backend patches
    // the cached parent RDL and ships it back. Refresh the RDL pane so the
    // user re-downloads the COMPLETED parent, not the stale one.
    if (j.parent_synced && j.parent_rdl_xml && state.data) {
      state.data.rdl_xml = j.parent_rdl_xml;
      try { renderRdlTab(state.data); } catch (e) { console.error(e); }
      toast("Parent RDL re-synced — its link now opens '" +
            (j.report_name || child) + "'. RE-DOWNLOAD the parent .rdl.",
            "ok");
      setMsg("Built. Parent re-synced — re-download the parent .rdl too.");
    } else {
      setMsg("Built — preview ready.");
    }
    toast("Sub-report " + child + " generated", "ok");
    return j;
  } catch (err) {
    setMsg("Build failed: " + ((err && err.message) || err));
    toast("Sub-report build failed", "err");
    return null;
  }
}

function subAddManual() {
  const raw = (window.prompt("Name for the sub-report (e.g. CHILD_REPORT):") || "").trim();
  if (!raw) return;
  const name = raw.replace(/[^A-Za-z0-9_-]/g, "_");
  state.subreportChildren = state.subreportChildren || [];
  if (!state.subreportChildren.some(c => c.name === name)) {
    state.subreportChildren.push({ name: name, detected: false, link: null, artifacts: [] });
  }
  const tabBtn = document.getElementById("tabbtn-subreports");
  if (tabBtn) tabBtn.hidden = false;
  renderSubreportsTab();
  renderSubreportSidebar();
  activateTab("subreports");
}


function escHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}




// ----- Recent reports (localStorage) -----
const RECENT_KEY = "o2s_recent_reports_v1";
const RECENT_MAX = 12;

function loadRecent() {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}
function saveRecent(arr) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(arr.slice(0, RECENT_MAX))); }
  catch (e) {}
}
function pushRecent(data) {
  if (!data || !data.report) return;
  const r = data.report;
  const entry = {
    name:    r.name || "Untitled",
    dtd:     r.dtd_version || "",
    params:  (r.parameters || []).length,
    queries: (r.queries || []).length,
    formulas:(r.formulas || []).length,
    rdl_size:(data.rdl_xml || "").length,
    ts:      Date.now(),
  };
  let list = loadRecent();
  // De-dupe by name (latest wins)
  list = list.filter(e => e.name !== entry.name);
  list.unshift(entry);
  saveRecent(list);
  renderRecentList();
}
function clearRecent() {
  try { localStorage.removeItem(RECENT_KEY); } catch (e) {}
  renderRecentList();
}
function relTime(ts) {
  const s = Math.max(0, (Date.now() - ts) / 1000);
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s/60) + "m ago";
  if (s < 86400) return Math.round(s/3600) + "h ago";
  return Math.round(s/86400) + "d ago";
}
function renderRecentList() {
  const host = document.getElementById("recent-list");
  if (!host) return;
  const empty = document.getElementById("recent-empty");
  const list = loadRecent();
  // Remove all chips except the empty-note placeholder
  Array.from(host.querySelectorAll(".recent-chip")).forEach(n => n.remove());
  if (!list.length) {
    if (empty) empty.style.display = "";
    return;
  }
  if (empty) empty.style.display = "none";
  list.forEach(e => {
    const chip = document.createElement("div");
    chip.className = "sample-chip recent-chip";
    chip.innerHTML =
      '<div class="recent-chip-name">' + escHtml(e.name) + '</div>' +
      '<div class="recent-chip-meta">' +
      e.params + ' params &middot; ' + e.queries + ' queries &middot; ' + e.formulas + ' formulas' +
      ' &middot; ' + relTime(e.ts) + '</div>';
    host.appendChild(chip);
  });
}


function wireEverything() {
  console.log("[Oracle2SSRS] wiring DOM event listeners");

  // Tabs
  $$(".tab").forEach(t => {
    t.addEventListener("click", (e) => {
      e.preventDefault();
      console.log("[Oracle2SSRS] tab clicked:", t.dataset.tab);
      activateTab(t.dataset.tab);
    });
  });

  // Drop zone
  const dropZone = $("#drop-zone");
  const fileInput = $("#file-input");
  const filesInput = $("#file-input-files");
  const pickLink = $("#pick-files-link");

  if (dropZone) {
    if (fileInput) {
      dropZone.addEventListener("click", (e) => {
        if (e.target === pickLink) return; // don't double-trigger
        fileInput.click();
      });
      dropZone.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
      });
      fileInput.addEventListener("change", () => {
        const list = Array.from(fileInput.files || []);
        if (list.length) handleFileList(list);
        fileInput.value = "";
      });
    }
    if (filesInput) {
      filesInput.addEventListener("change", () => {
        const list = Array.from(filesInput.files || []);
        if (list.length) handleFileList(list);
        filesInput.value = "";
      });
    }
    if (pickLink && filesInput) {
      pickLink.addEventListener("click", (e) => {
        e.preventDefault(); e.stopPropagation();
        filesInput.click();
      });
    }

    ["dragenter", "dragover"].forEach(evt =>
      dropZone.addEventListener(evt, (e) => {
        e.preventDefault(); e.stopPropagation();
        dropZone.classList.add("drag");
      })
    );
    ["dragleave", "dragend"].forEach(evt =>
      dropZone.addEventListener(evt, (e) => {
        e.preventDefault(); e.stopPropagation();
        dropZone.classList.remove("drag");
      })
    );
    dropZone.addEventListener("drop", async (e) => {
      e.preventDefault(); e.stopPropagation();
      dropZone.classList.remove("drag");
      console.log("[Oracle2SSRS] files dropped");

      const items = e.dataTransfer && e.dataTransfer.items;
      if (items && items.length && items[0].webkitGetAsEntry) {
        const entries = [];
        for (let i = 0; i < items.length; i++) {
          const it = items[i].webkitGetAsEntry && items[i].webkitGetAsEntry();
          if (it) entries.push(it);
        }
        try {
          const collected = [];
          for (const ent of entries) await walkEntry(ent, "", collected);
          if (collected.length) { handleFileList(collected); return; }
        } catch (err) {
          console.warn("folder walk failed, falling back", err);
        }
      }
      const flat = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
      if (flat.length) handleFileList(flat);
    });
    // Prevent the browser from navigating on stray drops outside the zone
    ["dragover", "drop"].forEach(evt =>
      window.addEventListener(evt, (e) => {
        if (!dropZone.contains(e.target)) e.preventDefault();
      })
    );
  } else {
    console.warn("[Oracle2SSRS] #drop-zone not found in DOM");
  }

  // Sample chips
  $$("#samples-list .sample-chip").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      console.log("[Oracle2SSRS] sample clicked:", btn.dataset.sample);
      runSample(btn.dataset.sample, btn);
    });
  });

  // Copy RDL
  const copyBtn = $("#copy-rdl");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      if (!state.data) return;
      try {
        await navigator.clipboard.writeText(state.data.rdl_xml || "");
        toast("Copied RDL to clipboard", "ok");
      } catch (e) { toast("Copy failed", "err"); }
    });
  }

  // Sync scrolling on side-by-side
  const left = $("#sxs-left-scroll");
  const right = $("#sxs-right-scroll");
  const cb = $("#sync-scroll");
  if (left && right && cb) {
    let lock = false;
    const onScroll = (src, dst) => () => {
      if (!cb.checked || lock) return;
      lock = true;
      const ratio = src.scrollTop / Math.max(1, src.scrollHeight - src.clientHeight);
      dst.scrollTop = ratio * Math.max(1, dst.scrollHeight - dst.clientHeight);
      requestAnimationFrame(() => { lock = false; });
    };
    left.addEventListener("scroll", onScroll(left, right));
    right.addEventListener("scroll", onScroll(right, left));
  }

  // Download .rdl
  const dlBtn = $("#download-rdl");
  if (dlBtn) {
    dlBtn.addEventListener("click", () => {
      if (!state.data) { toast("Convert a report first", "err"); return; }
      window.location.href = "/api/download/rdl";
    });
  }

  // Initial state
  activateTab("mockup");
  setStatus("Ready");
  wireSimplifiedUI();
  renderRecentList();
  const clearBtn = document.getElementById("recent-clear");
  if (clearBtn) clearBtn.addEventListener("click", clearRecent);
  const subAddBtn = document.getElementById("subreport-add-manual");
  if (subAddBtn) subAddBtn.addEventListener("click", subAddManual);
  initSharedDsPath();
  initReportServerUrl();
  initGenerateAllLabel();
  initHowto();
  wireBatch();
  console.log("[Oracle2SSRS] ready");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", wireEverything);
} else {
  wireEverything();
}

// =========================================================================
// Plug-and-play: Distribution Settings form on the Bursting tab.
//
// The static HTML provides the form (#burst-form-panel + #bf-* inputs).
// We hydrate values, persist across tab switches via state.burstOverrides,
// debounce live "Update Preview" re-renders, and POST to two new endpoints:
//   POST /api/burst-preview         -> rebuilt 4-block JSON
//   POST /api/download/burst-pack   -> .zip stream
// =========================================================================

function _bfDefaultBody() {
  return "Hello,\n\nYour {ReportName} report for {BurstKey} is attached.\n\n- Reports";
}

function _bfReadForm() {
  const g = (id) => document.getElementById(id);
  const port = parseInt((g("bf-smtp-port") || {}).value, 10);
  return {
    SmtpServer:      (g("bf-smtp-host") || {}).value || "smtp.office365.com",
    SmtpPort:        Number.isFinite(port) ? port : 587,
    AuthMode:        (g("bf-auth-mode") || {}).value || "Office365",
    SmtpFrom:        (g("bf-sender") || {}).value || "[email protected]",
    SubjectTemplate: (g("bf-subject") || {}).value || "{ReportName} - {BurstKey}",
    BodyTemplate:    (g("bf-body") || {}).value || _bfDefaultBody(),
    EmailBurstSql:   (g("bf-sql") || {}).value || "",
  };
}

function _bfWriteForm(o) {
  const g = (id) => document.getElementById(id);
  if (!o) return;
  if (g("bf-smtp-host"))  g("bf-smtp-host").value  = o.SmtpServer || "smtp.office365.com";
  if (g("bf-smtp-port"))  g("bf-smtp-port").value  = (o.SmtpPort != null ? o.SmtpPort : 587);
  if (g("bf-auth-mode"))  g("bf-auth-mode").value  = o.AuthMode || "Office365";
  if (g("bf-sender"))     g("bf-sender").value     = o.SmtpFrom || "[email protected]";
  if (g("bf-subject"))    g("bf-subject").value    = o.SubjectTemplate || "{ReportName} - {BurstKey}";
  if (g("bf-body"))       g("bf-body").value       = o.BodyTemplate || _bfDefaultBody();
  if (g("bf-sql") && o.EmailBurstSql != null) g("bf-sql").value = o.EmailBurstSql;
}

function hydrateBurstForm(data, burst) {
  const panel = document.getElementById("burst-form-panel");
  if (!panel) return;

  if (!burst || !burst.is_bursting) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  if (!state.burstOverrides) {
    state.burstOverrides = {
      SmtpServer:      "smtp.office365.com",
      SmtpPort:        587,
      AuthMode:        "Office365",
      SmtpFrom:        "[email protected]",
      SubjectTemplate: "{ReportName} - {BurstKey}",
      BodyTemplate:    _bfDefaultBody(),
      EmailBurstSql:   burst.email_burst_query || "",
    };
  } else if (!state.burstOverrides.EmailBurstSql && burst.email_burst_query) {
    state.burstOverrides.EmailBurstSql = burst.email_burst_query;
  }
  _bfWriteForm(state.burstOverrides);

  if (!panel._wired) {
    panel._wired = true;

    const debounce = (fn, ms) => {
      let t = null;
      return function() {
        const args = arguments;
        clearTimeout(t);
        t = setTimeout(() => fn.apply(null, args), ms);
      };
    };

    const triggerPreview = debounce(() => {
      state.burstOverrides = _bfReadForm();
      _burstPreview(state.burstOverrides);
    }, 300);

    ["bf-smtp-host","bf-smtp-port","bf-auth-mode","bf-sender",
     "bf-subject","bf-body","bf-sql"].forEach((id) => {
      const ele = document.getElementById(id);
      if (!ele) return;
      ele.addEventListener("input", () => {
        state.burstOverrides = _bfReadForm();
        triggerPreview();
      });
      ele.addEventListener("change", () => {
        state.burstOverrides = _bfReadForm();
        triggerPreview();
      });
    });

    const upd = document.getElementById("bf-update");
    if (upd) upd.addEventListener("click", () => {
      state.burstOverrides = _bfReadForm();
      _burstPreview(state.burstOverrides);
    });

    const dl = document.getElementById("bf-download");
    if (dl) dl.addEventListener("click", () => {
      state.burstOverrides = _bfReadForm();
      _burstPackDownload(state.burstOverrides);
    });
  }
}

function _burstPreview(overrides) {
  const status = document.getElementById("bf-status");
  if (status) status.textContent = "Updating preview...";
  fetch("/api/burst-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config_overrides: overrides || {} }),
  }).then((r) => r.json()).then((resp) => {
    if (resp && !resp.error && state.data) {
      state.data.bursting = Object.assign({}, state.data.bursting, {
        email_burst_query:         resp.email_burst_query,
        email_powershell_script:   resp.email_powershell_script,
        email_config_template:     resp.email_config_template,
        service_account_checklist: resp.service_account_checklist,
      });
      renderBurstingTab(state.data);
      if (status) status.textContent = "Preview updated.";
    } else {
      if (status) status.textContent = (resp && resp.error) || "Preview failed.";
    }
  }).catch((err) => {
    if (status) status.textContent = "Preview failed: " + ((err && err.message) || err);
  });
}

function _burstPackDownload(overrides) {
  const status = document.getElementById("bf-status");
  if (status) status.textContent = "Building burst pack...";
  fetch("/api/download/burst-pack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config_overrides: overrides || {},
                           shared_ds_path: getSharedDsPath() }),
  }).then((r) => {
    if (!r.ok) {
      return r.json().then((j) => { throw new Error((j && j.error) || ("HTTP " + r.status)); });
    }
    return r.blob().then((blob) => {
      const dispo = r.headers.get("Content-Disposition") || "";
      let nm = "burst_pack.zip";
      const m = /filename="?([^"]+)"?/.exec(dispo);
      if (m) nm = m[1];
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = nm;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (status) status.textContent = "Downloaded " + nm + ".";
    });
  }).catch((err) => {
    if (status) status.textContent = "Download failed: " + ((err && err.message) || err);
    toast("Burst pack download failed", "err");
  });
}


// ---------------------------------------------------------------------------
// Artifact stacking: banner + 'Add more artifacts' file picker
// ---------------------------------------------------------------------------

function renderEnrichmentBanner(data) {
  var prior = document.getElementById('enrich-banner');
  if (prior) prior.remove();
  var adder = document.getElementById('enrich-add-more-row');
  if (adder) adder.remove();
  var host = document.getElementById('ingest-summary')
         || document.getElementById('summary-section')
         || document.body;
  if (!host) return;
  var e = data && data.artifacts_enriched;
  if (e) {
    var sqlAdd = +e.sql_added || 0;
    var sqlRep = +e.sql_replaced || 0;
    var labels = +e.label_overrides || 0;
    var hints  = Array.isArray(e.hints) ? e.hints.length : 0;
    var bits = [];
    if (sqlAdd) bits.push(sqlAdd + ' SQL file' + (sqlAdd === 1 ? '' : 's') + ' added');
    if (sqlRep) bits.push(sqlRep + ' query SQL upgraded');
    if (labels) bits.push(labels + ' column label' + (labels === 1 ? '' : 's'));
    if (hints)  bits.push(hints  + ' layout hint' + (hints  === 1 ? '' : 's'));
    var banner = document.createElement('div');
    banner.id = 'enrich-banner';
    banner.className = 'enrich-banner';
    var label = document.createElement('span');
    label.className = 'enrich-banner-label';
    label.textContent = 'Enriched bundle';
    var msg = document.createElement('span');
    msg.textContent = bits.length ? bits.join(' \u00b7 ') : 'no changes applied';
    banner.appendChild(label);
    banner.appendChild(msg);
    host.insertBefore(banner, host.firstChild);
  }
  if (data && (data.rdl_xml || data.report)) {
    var row = document.createElement('div');
    row.id = 'enrich-add-more-row';
    row.className = 'enrich-add-row';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = '+ Add more artifacts';
    btn.className = 'btn btn-ghost btn-enrich-add';
    btn.addEventListener('click', openEnrichmentPicker);
    row.appendChild(btn);
    host.insertBefore(row, host.firstChild);
  }
}

function openEnrichmentPicker() {
  var inp = document.getElementById('enrich-file-input');
  if (inp) inp.remove();
  inp = document.createElement('input');
  inp.type = 'file';
  inp.id = 'enrich-file-input';
  inp.multiple = true;
  inp.style.display = 'none';
  inp.addEventListener('change', function() {
    var picked = Array.from(inp.files || []);
    if (!picked.length) { inp.remove(); return; }
    postEnrichmentBundle(picked).finally(function() { inp.remove(); });
  });
  document.body.appendChild(inp);
  inp.click();
}

function postEnrichmentBundle(newFiles) {
  setStatus('Re-converting with ' + newFiles.length + ' new artifact(s)...', 'busy');
  var fd = new FormData();
  var cached = state && state.data && state.data.oracle_xml;
  if (cached) {
    var xmlName = 'report.xml';
    try {
      if (state.data && state.data.report && state.data.report.name) {
        xmlName = state.data.report.name + '.xml';
      }
    } catch (e) {}
    fd.append('files', new Blob([cached], { type: 'application/xml' }), xmlName);
  }
  newFiles.forEach(function(f) { fd.append('files', f, f.name); });
  appendDeployFields(fd);
  fd.append('target_db', getTargetDb());
  return fetch('/api/convert-bundle', { method: 'POST', body: fd })
    .then(function(res) { return res.json().then(function(json) { return {res:res, json:json}; }); })
    .then(function(rj) {
      var res = rj.res, json = rj.json;
      if (!res.ok) throw new Error(json.error || 'Bundle re-conversion failed');
      if (json.error && json.error !== 'no_convertible_artifacts') {
        throw new Error(json.error);
      }
      onConverted(json);
      toast('Bundle re-converted with new artifacts', 'ok');
    })
    .catch(function(err) {
      console.error('[Oracle2SSRS] enrich re-convert failed:', err);
      setStatus('Error', 'err');
      toast((err && err.message) || 'Failed to add artifacts', 'err');
    });
}
