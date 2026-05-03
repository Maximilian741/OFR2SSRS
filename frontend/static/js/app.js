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
  $$(".tab-panel").forEach(p => { p.hidden = (p.id !== "tab-" + name); });
  const empty = $("#empty-state");
  if (empty) empty.hidden = !!state.data;
  // Re-highlight Prism content if any in the active panel
  if (window.Prism) Prism.highlightAll();
  // Always reset tab-panels scroll to top so new content is visible
  const panels = $(".tab-panels");
  if (panels) panels.scrollTop = 0;
}

// ----- API calls -----
async function uploadFile(file) {
  setStatus("Converting…", "busy");
  const fd = new FormData();
  fd.append("file", file);
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
  try {
    const res = await fetch("/api/convert-bundle", { method: "POST", body: fd });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Bundle conversion failed");
    if (json.error === "no_convertible_artifacts") {
      setStatus("Nothing to convert", "err");
      renderIngestSummary(json.ingest_report || {});
      toast("Found files but couldn't build a report — see Ingest Summary", "err");
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
    const res = await fetch("/api/convert-sample/" + encodeURIComponent(name), { method: "POST" });
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
  state.data = data;
  setStatus("Converted", "ok");
  if ($("#empty-state")) $("#empty-state").hidden = true;
  renderSummary(data);
  if (data.ingest_report) renderIngestSummary(data.ingest_report);
  renderMockupTab(data);
  renderRdlTab(data);
  renderSideBySideTab(data);
  renderLiveTab(data);
  renderValidationTab(data);
  renderDeploymentTab(data);
  renderExtrasTab(data);
  renderWarnings(data);
  if (window.Prism) Prism.highlightAll();
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
  if ($("#summary-section")) $("#summary-section").hidden = false;
}

// ----- Ingest summary panel (above tabs) -----
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
function renderMockupTab(data) {
  const host = $("#mockup-host");
  if (host) host.innerHTML = data.mockup_html || "<em>No mockup available.</em>";
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

// ----- Tab 5: Validation -----
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
    host.appendChild(el("div", { class: "results-empty", text: "No validation issues — looks good." }));
    return;
  }
  ["error", "warning", "info"].forEach(sev => {
    const matching = issues.filter(i => (i.severity || "info") === sev);
    if (!matching.length) return;
    host.appendChild(el("h3", { class: "sev-head sev-" + sev, text: sev.toUpperCase() + " (" + matching.length + ")" }));
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

  const footer = $("#warnings-footer");
  const list = $("#wf-list");
  if (list) {
    list.innerHTML = "";
    items.forEach(it => list.appendChild(el("li", {}, el("b", { text: it.scope + ": " }), document.createTextNode(it.text))));
  }
  if ($("#wf-count")) $("#wf-count").textContent = items.length;
  if (footer) footer.hidden = items.length === 0;

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


// ----- Tab 7: Extras (audit trail, AI prompts, bursting / DDS) -----
function renderExtrasTab(data) {
  const host = document.getElementById("extras-host");
  if (!host) return;
  host.innerHTML = "";

  // Section: Bursting
  const burst = (data && data.bursting) || {};
  const burstSection = document.createElement("section");
  burstSection.className = "extras-section";
  const burstTitle = burst.is_bursting ? "Bursting / Data-Driven Subscriptions DETECTED" : "Bursting / DDS — not detected";
  burstSection.innerHTML = "<h3>" + burstTitle + "</h3>";
  if (burst.is_bursting) {
    const ev = (burst.evidence || []).map(e => "<li>" + escapeHtml(e) + "</li>").join("");
    burstSection.innerHTML +=
      "<div class='extras-meta'>" +
      "<b>Burst key:</b> " + escapeHtml(burst.burst_key_field || "?") +
      " &nbsp;|&nbsp; <b>Filename pattern:</b> " + escapeHtml(burst.filename_pattern || "?") +
      "</div>" +
      "<details open><summary>Evidence (" + (burst.evidence || []).length + ")</summary><ul>" + ev + "</ul></details>";
    if (burst.burst_query) {
      burstSection.innerHTML +=
        "<details><summary>Burst query (T-SQL) <button class='btn btn-ghost btn-copy' data-copy='burst_query'>Copy</button></summary>" +
        "<pre class='code-block'><code class='language-sql'>" + escapeHtml(burst.burst_query) + "</code></pre></details>";
    }
    if (burst.powershell_script) {
      burstSection.innerHTML +=
        "<details><summary>PowerShell DDS emulator (for SSRS Standard) <button class='btn btn-ghost btn-copy' data-copy='powershell_script'>Copy</button></summary>" +
        "<pre class='code-block'><code>" + escapeHtml(burst.powershell_script) + "</code></pre></details>";
    }
  } else {
    burstSection.innerHTML += "<p class='extras-meta'>This report does not appear to use Oracle Reports distribution. No DDS skeleton generated.</p>";
  }
  host.appendChild(burstSection);

  // Section: AI prompts
  const prompts = data.ai_prompts || [];
  const promptSection = document.createElement("section");
  promptSection.className = "extras-section";
  promptSection.innerHTML = "<h3>AI-assist prompts (" + prompts.length + ")</h3>" +
    "<p class='extras-meta'>Paste any of these into Claude / Copilot / ChatGPT to get a working translation for the trickier PL/SQL. We don't call any LLM here.</p>";
  prompts.forEach((p, idx) => {
    const card = document.createElement("details");
    card.className = "extras-prompt diff-" + (p.difficulty || "medium");
    card.innerHTML =
      "<summary>" +
      "<span class='extras-tag'>" + escapeHtml(p.scope || "") + "</span> " +
      "<b>" + escapeHtml(p.name || ("prompt #" + (idx+1))) + "</b>" +
      " <span class='extras-difficulty'>" + escapeHtml(p.difficulty || "medium") + "</span>" +
      " <button class='btn btn-ghost btn-copy' data-copy-prompt='" + idx + "'>Copy prompt</button>" +
      "</summary>" +
      "<div class='extras-meta'>" + escapeHtml(p.context_hint || "") + "</div>" +
      "<pre class='code-block'><code>" + escapeHtml(p.prompt_template || "") + "</code></pre>";
    promptSection.appendChild(card);
  });
  host.appendChild(promptSection);

  // Section: Audit trail (table)
  const trail = data.audit_trail || [];
  const auditSection = document.createElement("section");
  auditSection.className = "extras-section";
  auditSection.innerHTML = "<h3>Translation audit trail (" + trail.length + " entries)</h3>" +
    "<p class='extras-meta'>Every translation decision recorded for review.</p>";
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
  console.log("[Oracle2SSRS] ready");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", wireEverything);
} else {
  wireEverything();
}
