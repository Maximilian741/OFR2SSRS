/* =========================================================
   Oracle2SSRS — frontend logic (clean rebuild)
   Vanilla JS. No IIFE, no fancy tricks. If anything breaks
   we want it visible in the console.
   ========================================================= */

console.log("[Oracle2SSRS] app.js loaded at", new Date().toLocaleTimeString());

// ----- State -----
const state = { data: null, activeTab: "mockup",
                // Last source + label overrides, so "Apply labels"
                // re-runs the SAME artifact through the real pipeline.
                lastFile: null, lastBundle: null };

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

// =========================================================================
// STATUS SURFACE — visible, persistent, plain language.
//
// THE BUG THIS EXISTS FOR, measured on the real page: a conversion failed,
// a toast showed the reason for three seconds, and then the only trace left
// anywhere was the word "Error" in an 11px pill in the corner. The reason
// itself lived in a title= tooltip on a <span> nobody can focus. Meanwhile
// the main column still showed the PREVIOUS report, so a user who looked
// away came back to a healthy-looking screen next to a tiny red badge.
//
// The person who runs this tool every day is deaf. There is no audio
// channel to fall back on and no colleague shouting "it failed" across the
// room, so a message that disappears is a message that never happened.
//
// The rules this file keeps (tests/test_status_surface.py enforces them):
//
//   * ONE surface. #activity-now (role=status, polite) says what is
//     happening; #activity-alert (role=alert, assertive) says what failed;
//     #activity-log is the durable record of the last dozen operations,
//     with the file name, the result and the time.
//   * PERSISTENT. Nothing here expires on a timer. A failure stays until it
//     is dismissed or until the next operation supersedes it.
//   * WHAT / WHY / NEXT. Every failure answers all three, in plain words,
//     on screen. Never "Failed to fetch", never "see the console".
//   * NEVER COLOUR ALONE. Every state is a GLYPH + a WORD + a SHAPE (the
//     card's left edge, from CSS). Colour is the fourth signal, not the
//     first.
//   * TOASTS ARE COURTESY COPIES. toast() still pops, but everything it
//     says is written into this surface first, so losing the toast loses
//     nothing.
// =========================================================================

// The vocabulary. Adding a state means adding all three signals at once.
const STATUS_STATES = {
  idle:    { glyph: "○", word: "Ready" },       // ○
  working: { glyph: "⟳", word: "Working" },     // ⟳
  done:    { glyph: "✓", word: "Done" },        // ✓
  warn:    { glyph: "⚠", word: "Check this" },  // ⚠
  failed:  { glyph: "✕", word: "Failed" },      // ✕
};
function statusState(name) {
  return STATUS_STATES[name] || STATUS_STATES.idle;
}

const _activity = { log: [], subject: "", lastLogAt: 0 };

// ---- card construction ---------------------------------------------------
// spec: {state, headline, subject, lines[], cmd, progress, dismiss}
function _statusCard(spec) {
  const st = statusState(spec.state);
  const card = el("div", { class: "activity-card", "data-state": spec.state });
  // What this card knows, in the markup: the failure's structural kind and
  // how much it actually tells the user. Both are read back off the DOM --
  // by the courtesy guard in statusFail, and by the tests -- so the check is
  // always against what is ON SCREEN rather than a flag that can drift.
  if (spec.kind) card.setAttribute("data-kind", spec.kind);
  if (spec.score != null) card.setAttribute("data-info-score", String(spec.score));
  // The glyph is decoration for a screen reader: the WORD beside it says
  // the same thing, and announcing "check mark Done" reads as a stutter.
  card.appendChild(el("span", { class: "activity-glyph", "aria-hidden": "true",
                                text: st.glyph }));
  card.appendChild(el("div", { class: "activity-head" },
    el("span", { class: "activity-state", text: st.word }),
    el("span", { class: "activity-headline", text: spec.headline || "" }),
    spec.subject ? el("span", { class: "activity-subject", text: spec.subject })
                 : null));
  const lines = (spec.lines || []).filter(Boolean);
  if (lines.length || spec.cmd) {
    const box = el("div", { class: "activity-lines" });
    lines.forEach(t => box.appendChild(el("p", { class: "activity-line", text: t })));
    // A command the user has to run belongs ON SCREEN, selectable. The .rdf
    // path used to print it to the browser console only, which is not a
    // place a report writer can be asked to go.
    if (spec.cmd) box.appendChild(el("code", { class: "activity-cmd", text: spec.cmd }));
    card.appendChild(box);
  }
  if (spec.progress) card.appendChild(_progressBlock(spec.progress));
  if (spec.dismiss) {
    card.appendChild(el("div", { class: "activity-actions" },
      el("button", { class: "activity-dismiss", type: "button",
                     onClick: spec.dismiss }, "Dismiss")));
  }
  return card;
}

// A bar is never the message. The label beside it says the same thing in
// words, and when the fraction is unknown the bar SAYS it is unknown --
// aria-valuenow is left off (that is how ARIA spells "indeterminate") and
// the label admits there is no percentage rather than inventing one.
function _progressBlock(p) {
  const wrap = el("div", { class: "activity-progress" });
  const known = typeof p.value === "number" && typeof p.max === "number"
                && p.max > 0 && p.value >= 0;
  const pct = known
    ? Math.max(0, Math.min(100, Math.round((p.value / p.max) * 100))) : null;
  const bar = el("div", {
    class: "activity-bar",
    "data-determinate": known ? "yes" : "no",
    role: "progressbar",
    // aria-live off: the visible label is inside the polite region already,
    // so the bar itself must not announce a second time.
    "aria-label": p.label || "Progress",
  });
  if (known) {
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-valuenow", String(pct));
    bar.setAttribute("aria-valuetext", pct + "%");
  }
  const fill = el("span", { class: "activity-bar-fill" });
  if (known) fill.style.width = pct + "%";
  bar.appendChild(fill);
  wrap.appendChild(bar);
  wrap.appendChild(el("p", { class: "activity-progress-label",
    text: known ? (pct + "% — " + (p.label || ""))
                : ((p.label || "Working") +
                   " · this step has no percentage: the server does it "
                   + "in one go and answers when it is finished") }));
  return wrap;
}

// ---- the two live regions ------------------------------------------------
function _statusHost(id) { return document.getElementById(id); }

function statusNow(spec) {
  const host = _statusHost("activity-now");
  if (!host) return;
  host.innerHTML = "";
  host.appendChild(_statusCard(spec));
  renderActivityLog();
  _syncPill();
}
function statusClearNow() {
  const host = _statusHost("activity-now");
  if (host) host.innerHTML = "";
  renderActivityLog();
  _syncPill();
}
function statusClearAlert() {
  const host = _statusHost("activity-alert");
  if (host) host.innerHTML = "";
  document.body.classList.remove("o2s-stale");
  renderActivityLog();
  _syncPill();
}

// What the status surface is saying RIGHT NOW, read back off the DOM. A
// failure outranks progress: while a failure card stands, that is the state
// of the app. Reading the elements themselves (rather than a remembered
// object) means the answer cannot drift away from what the user can see.
function currentStatus() {
  const alertCard = (_statusHost("activity-alert") || {}).firstElementChild;
  const nowCard = (_statusHost("activity-now") || {}).firstElementChild;
  const card = alertCard || nowCard;
  if (!card) return { state: "idle", headline: "Ready", subject: "", detail: "" };
  const txt = sel => {
    const n = card.querySelector(sel);
    return n ? (n.textContent || "").trim() : "";
  };
  const lines = Array.from(card.querySelectorAll(".activity-line"))
    .map(n => (n.textContent || "").trim());
  return {
    state: card.getAttribute("data-state") || "idle",
    headline: txt(".activity-headline"),
    subject: txt(".activity-subject"),
    detail: lines.join(" "),
    kind: card.getAttribute("data-kind") || "",
  };
}

// Is this reason ALREADY on screen? setStatus() and toast() are usually
// called as a pair with the same words; the structured call has already
// written the good version, and this stops the plain one overwriting it.
function _alreadyOnScreen(text) {
  if (!text) return false;
  const bits = ["activity-now", "activity-alert"].map(id => {
    const n = _statusHost(id);
    return n ? (n.textContent || "") : "";
  }).join(" ");
  return bits.indexOf(String(text)) !== -1;
}

// ---- the durable record --------------------------------------------------
function statusRecord(stateName, headline, opts) {
  opts = opts || {};
  const stamp = Date.now();
  const entry = {
    state: STATUS_STATES[stateName] ? stateName : "done",
    headline: String(headline || ""),
    subject: opts.subject || "",
    detail: opts.detail || "",
    time: new Date().toLocaleTimeString(),
  };
  const prev = _activity.log[_activity.log.length - 1];
  // setStatus + toast fire together at most failure sites. One event, one
  // line in the record.
  if (prev && prev.state === entry.state && prev.headline === entry.headline
      && (stamp - _activity.lastLogAt) < 2000) return;
  _activity.lastLogAt = stamp;
  _activity.log.push(entry);
  while (_activity.log.length > 12) _activity.log.shift();
  renderActivityLog();
}

function renderActivityLog() {
  const wrap = _statusHost("activity-history");
  const list = _statusHost("activity-log");
  if (!wrap || !list) return;
  list.innerHTML = "";
  // Newest first: the thing you want after looking away is the last thing
  // that happened, not the first. The newest entry is skipped when the card
  // above IS that outcome -- printing it twice, an inch apart, reads as two
  // separate events.
  const shown = ["activity-now", "activity-alert"]
    .map(id => { const n = _statusHost(id); return n ? (n.textContent || "") : ""; })
    .join(" ");
  let entries = _activity.log.slice().reverse();
  if (entries.length && entries[0].headline
      && shown.indexOf(entries[0].headline) !== -1) entries = entries.slice(1);
  entries.forEach(e => {
    const st = statusState(e.state);
    list.appendChild(el("li", { "data-state": e.state },
      el("span", { class: "log-glyph", "aria-hidden": "true", text: st.glyph }),
      el("span", { class: "log-state", text: st.word }),
      el("time", { class: "log-time", text: e.time }),
      el("span", { class: "log-text",
        text: (e.subject ? e.subject + " — " : "") + e.headline
              + (e.detail ? " — " + e.detail : "") })));
  });
  wrap.hidden = entries.length === 0;
}

// ---- the operation API ---------------------------------------------------
// Long operation begins. `progress` is optional; without it the card shows
// an honest indeterminate bar with a text label.
function statusBegin(subject, headline, progress) {
  _activity.subject = subject || "";
  statusClearAlert();                       // a new attempt supersedes the old
  statusNow({ state: "working", headline: headline, subject: subject,
              progress: progress || { label: headline } });
  // Deliberately NOT recorded: the log is a record of OUTCOMES. A running
  // operation is already on screen as the Working card, and logging the
  // start too pushed the actual results off the top of the list.
  _activity.beganAt = Date.now();
}

// Update the running card in place. value/max in BYTES gives a real
// percentage for the upload leg; pass nothing once the bytes are gone and
// the server is thinking, because then nobody knows the fraction.
function statusProgress(headline, label, value, max) {
  statusNow({ state: "working", headline: headline, subject: _activity.subject,
              progress: { label: label, value: value, max: max } });
}

function statusDone(headline, opts) {
  opts = opts || {};
  statusClearAlert();
  statusNow({ state: opts.state === "warn" ? "warn" : "done",
              headline: headline,
              subject: opts.subject != null ? opts.subject : _activity.subject,
              lines: opts.lines || [] });
  statusRecord(opts.state === "warn" ? "warn" : "done", headline,
               { subject: opts.subject != null ? opts.subject : _activity.subject,
                 detail: opts.detail || "" });
}

// How much a failure card actually TELLS the user. A card that carries the
// export command plus a real next step outranks one that carries a sentence
// and nothing else; the ranking is what stops a courtesy copy replacing the
// only useful thing on screen (see statusFail).
function _failInfoScore(spec) {
  return (spec.cmd ? 4 : 0) + (spec.next ? 2 : 0) + (spec.why ? 1 : 0);
}

// A courtesy copy (setStatus/toast fire beside the real call at the same
// site, in the same tick) belongs to the SAME event as the card already on
// screen. Anything later is a new event and always gets to speak.
const COURTESY_PAIR_MS = 1200;

// A failure. All three questions get answered, on screen, and it stays.
//
// opts.kind      structural code for what went wrong -> "Do this next"
// opts.courtesy  this is a restatement of a failure a primary call has
//                already reported (setStatus / toast). It may create the
//                card when there is none, but it must NEVER overwrite a
//                richer one.
//
// THE DEFECT THAT GUARD EXISTS FOR, measured: dropping a .rdf produced the
// one card that tells the operator how to fix it -- the rwconverter command,
// on screen, selectable -- and then the setStatus() and toast() beside it
// replaced that card, in the same tick, with "There was no Oracle XML
// export...". The instruction the server had returned was destroyed before
// the user could read it.
function statusFail(what, opts) {
  opts = opts || {};
  const why = opts.why || "";
  const next = opts.next || _nextActionFor(opts.kind);
  const subject = opts.subject != null ? opts.subject : _activity.subject;
  if (opts.courtesy) {
    const standing = (_statusHost("activity-alert") || {}).firstElementChild;
    const fresh = (Date.now() - (_activity.alertAt || 0)) < COURTESY_PAIR_MS;
    const have = standing
      ? Number(standing.getAttribute("data-info-score") || 0) : -1;
    if (standing && fresh
        && have >= _failInfoScore({ cmd: opts.cmd, next: opts.next, why: why })) {
      // Already said, better, by the call that actually knows. Keep the
      // card; the words still exist in the toast that triggered this.
      console.log("[Oracle2SSRS] courtesy failure copy kept off the card: "
                  + String(what).slice(0, 120));
      return;
    }
  }
  const lines = [];
  if (why && why !== what) lines.push("Why: " + why);
  lines.push("Do this next: " + next);
  // Invalidate what is still on screen. Without this the main column keeps
  // showing the last SUCCESSFUL conversion beside the failure, which reads
  // as "it worked".
  const prevName = (state && state.data && state.data.report
                    && state.data.report.name) || null;
  if (prevName) {
    lines.push("The report views below are still " + prevName
      + " from the previous conversion. Nothing on this screen was updated "
      + "by the step that just failed.");
    document.body.classList.add("o2s-stale");
  }
  const host = _statusHost("activity-alert");
  if (host) {
    host.innerHTML = "";
    _activity.alertAt = Date.now();
    host.appendChild(_statusCard({
      state: "failed", headline: what, subject: subject, lines: lines,
      cmd: opts.cmd || "", kind: opts.kind || "",
      score: _failInfoScore({ cmd: opts.cmd, next: opts.next, why: why }),
      dismiss: () => {
        statusClearAlert();
        statusNow({ state: "idle",
                    headline: "Ready. The failure above is kept under Earlier." });
      },
    }));
  }
  // Nothing is running any more, so the polite region must not keep saying
  // "Working" underneath a failure.
  statusClearNow();
  statusRecord("failed", what, { subject: subject, detail: why });
}

// ---- "Do this next", derived from a KIND and never from prose -----------
//
// THE DEFECT THIS REPLACES, measured on the real page. The next action used
// to be chosen by matching English words in the failure message
// (msg.indexOf("too large"), msg.indexOf("nothing"), ...). Those sentences
// are rewritten for clarity every few weeks, and the moment one changed the
// advice silently went WRONG while still sounding confident:
//
//   * a 61 KB drop the pre-flight had just refused for exceeding a 20 KB cap
//     ("Upload is 61 KB but this server accepts at most ...") matched none of
//     the patterns and was answered with "Try the same step again" -- which
//     fails identically, every time, forever;
//   * a dropped .rdf, and a drop with no report in it, got the same useless
//     "try again".
//
// Guessing the meaning of a string by looking for words in it is the same
// mistake as guessing an Oracle report's meaning from its name. So the next
// action is now looked up from a KIND: a short, stable code for WHAT WENT
// WRONG, decided where it went wrong. The server returns one as `error_kind`
// (see backend/app.py); a client-side failure sets one at the call site that
// already knows which check it just failed. No sentence is ever inspected,
// so rewriting any message in this file cannot change any advice.
//
// An unknown kind falls back to the honest "we don't know" line rather than
// inventing a step -- adding a failure without a kind degrades to vague, not
// to wrong.
const NEXT_ACTION_BY_KIND = {
  server_unreachable:
    "Start the app again with run.bat, wait for it to say it is listening, "
    + "then drop the file once more.",
  upload_too_large:
    "Drop only the Oracle Reports XML export (the .xml), plus any .sql or "
    + ".docx that belongs with it. Rendered PDFs and screenshots are "
    + "reference material — the conversion never reads them, and they are "
    + "what puts a folder over the size limit.",
  upload_interrupted:
    "Send less at once: drop the Oracle .xml on its own first, then add the "
    + "rest with “Add more artifacts” once the report is on screen.",
  no_report_in_drop:
    "Add the report's Oracle XML export (the .xml file) to the drop, then "
    + "try again. The Ingest Summary below lists what each file you dropped "
    + "was taken to be.",
  // The SAME sentence used to be given for the case below, and it sent the
  // operator to look at a panel that is not on the screen. This failure
  // happens in the browser, BEFORE anything is sent: every file in the drop
  // was reference material (a rendered PDF, a screenshot, an oversized
  // file), so the request was never made and no Ingest Summary was ever
  // built. Measured with the real artifact folder, whose 34.9 MB report PDF
  // is exactly the file that triggers it.
  reference_files_only:
    "Add the report's Oracle XML export — the .xml file — and any .sql or "
    + ".docx that belongs with it. Everything in this drop was reference "
    + "material (a rendered PDF, a screenshot, an oversized file); the "
    + "conversion never reads those, so nothing was sent and there is "
    + "nothing to look at below.",
  rdf_binary:
    "Run the export command shown above on a machine that has Oracle "
    + "Reports installed, then drop the .xml file it writes here.",
  no_report_yet:
    "Convert a report first: drop an Oracle XML export into the sidebar, or "
    + "click one of the sample reports under “Try a sample”. Then do this "
    + "again.",
  clipboard_blocked:
    "Select the text in the panel and press Ctrl+C instead.",
  render_engine_unavailable:
    "The .rdl itself is unaffected — download it and upload it to your "
    + "report server as usual. To get the preview here, set your report "
    + "server URL in the sidebar, or press Re-render once the report engine "
    + "can start on this machine.",
  image_rejected:
    "Check the file is a PNG, GIF or JPG and small enough, then add it "
    + "again. The report itself was not changed.",
  ai_not_configured:
    "This step needs the optional AI helper, which is not set up on this "
    + "machine. Nothing else depends on it — carry on with the conversion.",
  nothing_selected:
    "Choose the files above first, then press the button again.",
  invalid_request:
    "The server could not use what was sent. Start again from a fresh drop "
    + "of the Oracle XML export.",
  server_error:
    "The converter hit an unexpected error and stopped; nothing was saved. "
    + "The same file will fail the same way until it is fixed, so copy the "
    + "wording above together with the report name and send it to whoever "
    + "installed this tool.",
  validation_failed:
    "Open the Validation view, fix what it lists, then run this step again.",
};
const NEXT_ACTION_UNKNOWN =
  "Try the same step again. If it fails the same way, copy the wording "
  + "above and send it to whoever installed this tool.";

function _nextActionFor(kind) {
  const k = String(kind || "");
  // hasOwnProperty, not a bare lookup: a kind called "constructor" would
  // otherwise return a function and print [object Object] at the user.
  return Object.prototype.hasOwnProperty.call(NEXT_ACTION_BY_KIND, k)
    ? NEXT_ACTION_BY_KIND[k] : NEXT_ACTION_UNKNOWN;
}

// Other files (the guided tour, the AI helpers) get the same surface rather
// than inventing their own transient one.
window.o2sStatus = {
  begin: statusBegin, progress: statusProgress, done: statusDone,
  fail: statusFail, record: statusRecord, clearAlert: statusClearAlert,
  states: STATUS_STATES,
};

// ---- inline status lines (batch, burst pack, sub-report cards) -----------
// Same vocabulary in a smaller box. These elements used to be written with
// bare textContent, so "Downloaded burst_pack.zip." and "Download failed:"
// looked EXACTLY alike: same 11.5px grey, no icon, no shape, no word.
function setInlineStatus(node, stateName, text) {
  if (!node) return;
  const st = statusState(stateName);
  node.innerHTML = "";
  node.classList.add("inline-status");
  node.setAttribute("data-state", STATUS_STATES[stateName] ? stateName : "idle");
  // Progress and results here are read by eye, so the region is polite and
  // on-screen -- never sr-only.
  if (!node.getAttribute("aria-live")) node.setAttribute("aria-live", "polite");
  node.appendChild(el("span", { class: "inline-glyph", "aria-hidden": "true",
                                text: st.glyph }));
  node.appendChild(el("span", { class: "inline-state", text: st.word }));
  node.appendChild(el("span", { class: "inline-text", text: text || "" }));
}

// ----- Status / toast -----
// `detail` carries the REASON. A bare red "Error" badge with no explanation
// was half of this bug's user-visible surface: the toast vanishes after
// three seconds and the badge then says nothing at all.
//
// THE SECOND DEFECT, measured on the real page: the pill was a SECOND STATUS
// CHANNEL that disagreed with the first. Converting the bundled sample wrote
// an honest verdict card -- "⚠ Check this — Converted SAMPLE_FEE_NOTICE ...
// 1 thing may go wrong when the report actually runs" -- and then
// setStatus("Converted", "ok") painted a green "✓ CONVERTED" in the corner
// for the same event. Two states, same moment, one screen. Dropping a .rdf
// was worse: the pill's words replaced the card entirely.
//
// So the pill no longer decides anything. It is a PROJECTION of the status
// surface: setStatus offers its words to the surface (as a courtesy copy,
// which may never overwrite a better card) and the pill is then re-rendered
// FROM whatever the surface ended up saying. Every status write goes through
// _syncPill, so a contradiction is not merely unlikely, it is unspellable.
function setStatus(text, kind, detail) {
  const pill = $("#status-pill");
  if (!pill) return;
  // Words first, to the surface that keeps them...
  _mirrorStatus(text, kind, detail);
  // ...then the corner pill, rendered from that surface.
  _syncPill();
}

// The pill's whole content: the state's glyph, the state's word, and a short
// form of the headline the card is showing. Never its own wording.
const PILL_MAX_CHARS = 30;
const _PILL_CLASS = { working: "busy", done: "ok", warn: "warn",
                      failed: "err", idle: "" };
function _syncPill() {
  const pill = $("#status-pill");
  if (!pill) return;
  const cur = currentStatus();
  const st = statusState(cur.state);
  const head = cur.headline || st.word;
  const short = head.length > PILL_MAX_CHARS
    ? head.slice(0, PILL_MAX_CHARS - 1).trim() + "…" : head;
  const detail = [cur.subject, cur.detail].filter(Boolean).join(" — ")
                 || head;
  pill.textContent = "";
  pill.className = "topbar-pill has-glyph"
    + (_PILL_CLASS[cur.state] ? " " + _PILL_CLASS[cur.state] : "");
  pill.setAttribute("data-state", cur.state);
  // Colour is not a signal on its own: the pill carries the state's glyph,
  // and the words beside it are the card's own headline.
  pill.appendChild(el("span", { class: "pill-glyph", "aria-hidden": "true",
                                text: st.glyph }));
  pill.appendChild(el("span", { class: "pill-word", text: short }));
  // NO title= here, deliberately. The reason used to be parked in the pill's
  // tooltip -- which needs a mouse, never opens for a keyboard user, does not
  // exist on touch, and is announced inconsistently; the app forbids that
  // page-wide (tests/test_a11y_structure.py). It is no longer needed either:
  // every word this tooltip carried is on screen in the card below, and the
  // full sentence is in the pill's accessible name.
  pill.removeAttribute("title");
  // The state said in WORDS so the badge never depends on its tint, then the
  // card's own headline and reason.
  pill.setAttribute("aria-label", st.word + ": " + head
    + (detail && detail !== head ? " — " + detail : ""));
}

// The pill's words, promoted to the persistent surface. Skipped when a
// structured statusBegin/statusDone/statusFail has already written a better
// version of the same thing -- checked by looking at what is ON SCREEN, not
// by a flag that can drift.
function _mirrorStatus(text, kind, detail) {
  const reason = detail || text;
  if (kind === "err") {
    if (_alreadyOnScreen(reason)) return;
    // "Error" is not a headline. A pill call carries a CATEGORY word in
    // `text` and, when the site had one, the actual reason in `detail` -- so
    // the reason is the headline whenever a reason was supplied. That is
    // decided by WHICH ARGUMENT EXISTS, never by recognising the category
    // words: the old version matched a list of them ("error", "upload too
    // large", ...) and quietly stopped working on every copy rewrite.
    const headline = detail ? String(detail) : String(text);
    // No kind: a pill call knows a category word, not what actually broke.
    // The sites that DO know raise a primary statusFail with one, and this
    // courtesy copy is then declined below rather than replacing it.
    statusFail(headline, { why: "", courtesy: true });
    return;
  }
  if (kind === "busy") {
    // A structured statusBegin() a moment ago already wrote the good card
    // for this same operation; the pill's shorthand must not replace it.
    if (_alreadyOnScreen(text)) return;
    if ((Date.now() - (_activity.beganAt || 0)) < 3000) return;
    statusNow({ state: "working", headline: text, subject: _activity.subject,
                progress: { label: text } });
    return;
  }
  if (kind === "ok") {
    if (_alreadyOnScreen(text)) return;
    statusDone(text, { detail: detail || "" });
    return;
  }
  // No kind at all = the idle "Ready" state on first load.
  if (!_alreadyOnScreen(text)) {
    statusNow({ state: "idle", headline: text,
                lines: detail ? [detail] : [] });
  }
}

// "You have not converted anything yet" is a real failure with a real next
// step, not a three-second toast. One helper so every guard says it the same
// way and lands on the surface that keeps it.
function _needAReportFirst(what) {
  statusFail(what || "There is no report to do that with yet",
             { kind: "no_report_yet" });
  toast("Convert a report first", "err");
}

function toast(msg, kind) {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.className = "toast" + (kind ? " " + kind : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 3000);
  // A toast is a COURTESY COPY, never the record. Whatever it says is also
  // written to the status strip, so the three-second timer can take the
  // toast away without taking the information with it.
  if (kind === "err") {
    // courtesy: the site that raised this has usually already written a
    // fuller card. This may CREATE one, never replace a better one.
    if (!_alreadyOnScreen(msg)) statusFail(String(msg), { courtesy: true });
  } else {
    statusRecord(kind === "warn" ? "warn" : "done", String(msg));
  }
}

// ----- Syntax highlighting (targeted + size-guarded) -----
// `Prism` here is static/js/highlight.js, served from this machine — the
// page no longer fetches Prism from a CDN, because the workstation this
// runs on may be offline. The global keeps the Prism name and the Prism
// `.token` classes, so the notes below still describe what happens.
//
// highlightAll() re-tokenizes EVERY code block in the document. A
// real-world report produces a multi-megabyte RDL, and highlightAll on
// every tab switch froze the main thread for seconds per click (measured:
// 3 MB of XML = ~1.6 s for ONE block, and the document holds three copies
// — the RDL tab plus both side-by-side panes). To the user that reads as
// "switching tabs doesn't load the page". So:
//   * highlight ONLY the blocks inside the panel being shown,
//   * remember what was highlighted so re-visiting a tab is free,
//   * and above a size cap leave the block as plain text — instant, and
//     a 3 MB wall of XML gains nothing from coloring.
// Above this, skip syntax colors: a highlighted block is tens of thousands
// of <span>s, and un-hiding that tree costs ~270 ms of layout PER SWITCH
// even display-capped (measured). A plain text node lays out in tens of ms,
// and coloring adds little to machine-generated XML at that size anyway.
const PRISM_MAX_CHARS = 100000;
function highlightPanel(panel) {
  if (!window.Prism || !panel) return;
  panel.querySelectorAll('code[class*="language-"]').forEach(code => {
    const txt = code.textContent || "";
    const stamp = txt.length + ":" + (state._convSeq || 0);
    if (code._hlStamp === stamp) return;   // this exact content already done
    code._hlStamp = stamp;
    if (txt.length > PRISM_MAX_CHARS) return;  // too big: plain text
    try { Prism.highlightElement(code); } catch (e) {
      console.error("[Oracle2SSRS] highlight failed:", e);
    }
  });
}

// ----- Tabs -----
// The strip follows the ARIA tabs pattern, and the pattern is not optional
// decoration: without aria-selected a screen reader announces nine tabs and
// no current one, and without a ROVING TABINDEX every tab is its own stop,
// so Tab walks the whole strip instead of stepping over it to the content.
// The rule is: exactly one tab is in the tab order (the selected one), and
// the arrow keys move within the strip. syncTabRoving is the single place
// that keeps that true -- activateTab calls it, and so does the Advanced
// toggle, because hiding the group can hide the tab that held the stop.
function visibleTabs() {
  return $$('[role="tab"]').filter(t => t.offsetParent !== null);
}
function syncTabRoving() {
  const tabs = $$('[role="tab"]');
  tabs.forEach(t => {
    const on = t.dataset.tab === state.activeTab;
    t.setAttribute("aria-selected", on ? "true" : "false");
    t.tabIndex = on ? 0 : -1;
  });
  // If the selected tab is hidden (its group was collapsed), the strip
  // would hold NO tab stop at all and become unreachable by keyboard.
  const vis = visibleTabs();
  if (vis.length && !vis.some(t => t.tabIndex === 0)) vis[0].tabIndex = 0;
}
function activateTab(name) {
  state.activeTab = name;
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  syncTabRoving();
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
  // With no conversion yet, a tab click shows only the empty-state hero —
  // SAY so, or the tabs read as broken (user-reported twice).
  if (!hasData && activateTab._userClicked) {
    toast("Convert a report first — drop an Oracle XML or click a sample.",
          "warn");
  }
  activateTab._userClicked = false;
  // Highlight only what just became visible (see highlightPanel).
  highlightPanel($("#tab-" + name));
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
// "Standard 12 x 9 Envelope"). Not a secret -> persisted in localStorage.
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
//
// This modal and the guided tour are the two places a NON-EXPERT learns what
// the tool does. So the rules here are copy rules, not layout rules:
//   * short sentences, active voice, one idea per sentence;
//   * every SSRS term is explained the first time it appears -- there is a
//     glossary below, and nothing above it uses a word the glossary has not
//     already unpacked;
//   * the view names match the TAB LABELS exactly. The table used to say
//     "HTML Mockup" and "Deployment" for tabs actually labelled "Preview"
//     and "Deploy Checklist", which teaches a UI that is not on screen;
//   * every claim is true, including the unflattering ones. What the tool
//     cannot do has its own section, because that honesty is the reason to
//     trust the rest.
function _appHowtoHTML() {
  return (
    '<h2 id="howto-title">How Oracle2SSRS works</h2>' +
    '<p class="howto-lead">This tool takes a report exported from Oracle ' +
      'Reports and writes the same report for <b>SSRS</b> (SQL Server ' +
      'Reporting Services, Microsoft&rsquo;s reporting server). Four steps, ' +
      'below. Two extras &mdash; <b>sub-reports</b> and <b>bursting</b> &mdash; ' +
      'are explained further down, and every term used here is in the ' +
      'glossary at the end.</p>' +
    '<p><button id="howto-tour-start" class="btn btn-primary" type="button">' +
      '&#9654; Start the guided tour</button> &nbsp;<span class="muted-note">' +
      'It converts a sample report and walks you through every view, one ' +
      'step at a time. Press Escape to stop.</span></p>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">The basic flow</h3>' +
      '<div class="howto-pipe">' +
        '<div class="howto-pipe-step"><span class="o2s-num">1</span>Drop the files<small>the Oracle <code>.xml</code> or <code>.rdf</code> export, plus any <code>.sql</code>, <code>.docx</code> or image files that go with it</small></div>' +
        '<div class="howto-pipe-arr">&rarr;</div>' +
        '<div class="howto-pipe-step"><span class="o2s-num">2</span>Convert<small>look at the preview, and at the report file this tool wrote</small></div>' +
        '<div class="howto-pipe-arr">&rarr;</div>' +
        '<div class="howto-pipe-step"><span class="o2s-num">3</span>Point it at your data<small>type your shared data source in the panel on the left, before you download</small></div>' +
        '<div class="howto-pipe-arr">&rarr;</div>' +
        '<div class="howto-pipe-step"><span class="o2s-num">4</span>Download and upload<small>put the <code>.rdl</code> file on your report server</small></div>' +
      '</div>' +
      '<p>One thing to remember at step 4: <b>do not click &ldquo;Refresh ' +
        'Fields&rdquo;</b> in Report Builder. This tool has already written ' +
        'the complete field list into the file. Refreshing makes Report ' +
        'Builder run the query itself, which is what makes it ask you for ' +
        'every parameter.</p>' +
    '</div>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">Reading the verdict</h3>' +
      '<p>Every conversion ends with one word. It is on the left, under the ' +
        'report summary, and again above the report views. It is the first ' +
        'thing to read.</p>' +
      '<table class="howto-views">' +
      '<caption class="sr-only">What each verdict word means</caption>' +
      '<thead><tr><th scope="col">Verdict</th><th scope="col">What it means</th></tr></thead><tbody>' +
      '<tr><th scope="row"><b>BLOCKER</b></th><td>Do not upload this yet. The file will not load and refresh cleanly on the server. Open <b>Validation</b>, fix what it lists, convert again.</td></tr>' +
      '<tr><th scope="row"><b>RED</b></th><td>It will upload, but something is likely to come out wrong when the report actually runs. <b>Validation</b> says what and why.</td></tr>' +
      '<tr><th scope="row"><b>AMBER</b></th><td>Worth reading, not blocking. The report should still run. These are notes about things a person may need to finish.</td></tr>' +
      '<tr><th scope="row"><b>READY</b></th><td>Nothing is standing in your way. Download it and upload it.</td></tr>' +
      '</tbody></table>' +
      '<p>Beside the verdict is the <b>fidelity</b> figure, a percentage. It ' +
        'answers one narrow question: how much of the original survived the ' +
        'conversion &mdash; the columns and parameters that reached the new ' +
        'file, and the columns Oracle put on the page that the new file ' +
        'actually prints. <b>100% does not mean the report looks right.</b> ' +
        'This tool never sees a picture of your original, so it cannot ' +
        'compare. Open <b>Preview</b> and check that yourself.</p>' +
    '</div>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">Sub-reports (one report opens another)</h3>' +
      '<p>A row in one report can open a <b>second</b> report about just that ' +
        'row. Each row carries its own values across. A separate ' +
        '&ldquo;generate all&rdquo; link produces the whole set <b>in the same ' +
        'order as the rows</b>.</p>' +
      '<div class="o2s-flow">' +
        '<div class="o2s-node o2s-main"><div class="o2s-node-title">main report</div><div class="o2s-node-sub">lists every record</div></div>' +
        '<div class="o2s-links">' +
          '<div class="o2s-link"><span class="o2s-num">1</span><div>click <b>one row</b> <span class="o2s-arrow">&rarr;</span> the second report for that row alone</div></div>' +
          '<div class="o2s-link"><span class="o2s-num">2</span><div><b>&ldquo;generate all&rdquo;</b> <span class="o2s-arrow">&rarr;</span> every one of them, <em>in row order</em></div></div>' +
        '</div>' +
        '<div class="o2s-node o2s-child"><div class="o2s-node-title">sub-report</div><div class="o2s-node-sub">e.g. one envelope</div></div>' +
      '</div>' +
      '<p>Build the sub-report in the <b>Sub-Reports</b> view by dropping its ' +
        'files there. Upload the <b>sub-report first</b>, then the main ' +
        'report: the link is matched by name at the moment someone clicks it, ' +
        'so the sub-report has to be there already. The links work in the ' +
        'SSRS viewer <b>and</b> in a PDF exported from it.</p>' +
    '</div>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">Bursting (one run, one PDF per person)</h3>' +
      '<p><b>Bursting</b> means running the report once and splitting the ' +
        'result into many PDFs &mdash; one per recipient &mdash; then emailing ' +
        'each one automatically. Each person receives only their own pages.</p>' +
      '<div class="o2s-flow">' +
        '<div class="o2s-node o2s-main"><div class="o2s-node-title">one run</div><div class="o2s-node-sub">many rows</div></div>' +
        '<div class="o2s-links">' +
          '<div class="o2s-link"><span class="o2s-num">1</span><div>split by recipient <span class="o2s-arrow">&rarr;</span> <b>one PDF each</b></div></div>' +
          '<div class="o2s-link"><span class="o2s-num">2</span><div>each PDF <span class="o2s-arrow">&rarr;</span> <b>emailed to that person</b> <em>(automatically)</em></div></div>' +
        '</div>' +
        '<div class="o2s-node o2s-child"><div class="o2s-node-title">per person</div><div class="o2s-node-sub">PDF + email</div></div>' +
      '</div>' +
      '<p>Set up the recipient list and download the ready-to-run pack in the ' +
        '<b>Bursting</b> view.</p>' +
    '</div>' +

    '<div class="burst-callout"><b>A worked example &mdash; a permit report.</b> ' +
      'The report lists every permit holder. Each permit links to <b>that ' +
      'holder&rsquo;s envelope</b>, which is a sub-report. A ' +
      '&ldquo;generate all&rdquo; link prints <b>every envelope in the same ' +
      'order as the permits</b>, so the two stacks line up for mailing.</div>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">What each view is for</h3>' +
      '<table class="howto-views">' +
      '<caption class="sr-only">What each view in Oracle2SSRS is for</caption>' +
      '<thead><tr><th scope="col">View</th><th scope="col">What it is for</th></tr></thead><tbody>' +
      '<tr><th scope="row"><b>Preview</b></th><td>What the report will look like, filled with sample data. <b>Frontend</b> shows the printed pages; <b>Backend</b> shows the empty layout, so you can see which data field sits in which box. Your &ldquo;does this look right?&rdquo; check.</td></tr>' +
      '<tr><th scope="row"><b>RDL XML</b></th><td>The report file this tool wrote &mdash; the file you upload. It is here so you can check it; you do not have to read it. A very long file shows only its beginning here, and every download contains all of it.</td></tr>' +
      '<tr><th scope="row"><b>Side-by-Side</b></th><td>The Oracle file on the left, the new file on the right. Search both for a column or query name to answer &ldquo;where did this end up?&rdquo; This is your record of how a number got onto the page.</td></tr>' +
      '<tr><th scope="row"><b>Live Data</b></th><td>Runs the report&rsquo;s SQL against a small read-only <b>sample database</b> that ships with this tool, to prove the SQL is valid and runs. Tables the sample database does not have come back empty. It does <b>not</b> touch your database. The connection you type on the left is baked into the downloaded RDL for the server to use later; it is not used here.</td></tr>' +
      '<tr><th scope="row"><b>Validation</b></th><td>Every check this tool ran, with what will happen at run time and what to do. See &ldquo;Reading the verdict&rdquo; above for what BLOCKER, RED, AMBER and READY mean.</td></tr>' +
      '<tr><th scope="row"><b>Deploy Checklist</b></th><td>The steps for going live, in order: where the data source goes, how to upload, and why you never click Refresh Fields. The download buttons are here too.</td></tr>' +
      '<tr><th scope="row"><b>Extras</b></th><td>How much of the original came across, a record of the decisions made while converting, and ready-made questions you can paste into an AI tool for a second opinion.</td></tr>' +
      '<tr><th scope="row"><b>Bursting</b></th><td>One run, one PDF per recipient, emailed automatically. This is what replaces Oracle&rsquo;s <code>distribute=YES</code>.</td></tr>' +
      '<tr><th scope="row"><b>Sub-Reports</b></th><td>The reports this one links to, such as envelopes or detail pages. Drop a sub-report&rsquo;s files here to build it; the links in the main report then work once both are on your server.</td></tr>' +
      '</tbody></table>' +
      '<p>Preview and RDL XML are always in view. The rest sit behind ' +
        '<b>Advanced views</b>. Bursting and Sub-Reports move out in front ' +
        'whenever the report you converted actually uses them.</p>' +
    '</div>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">What it handles</h3>' +
      '<ul>' +
        '<li>Plain lists, grouped reports, and master-detail reports.</li>' +
        '<li>One-per-record letters, invoices, certificates and permits, including seals and logos.</li>' +
        '<li>Cross-tab grids (a matrix, with values arranged by row and column), charts, and packets made of several sections.</li>' +
        '<li>Exports that are not in Unicode, such as Greek or Spanish text saved in an older encoding.</li>' +
        '<li>Formatting rules, such as &ldquo;print this in red when it is overdue&rdquo;.</li>' +
        '<li>Buttons printed inside the report, such as a &ldquo;Send Emails&rdquo; button, with the address they really point at.</li>' +
        '<li>Filters typed in at run time. In Oracle these are <b>lexicals</b>: named pieces of SQL text the report drops into its own query while it runs. Most become real SSRS parameters, so the prompts still filter. Where one cannot be handled automatically, <b>Validation</b> and <b>Deploy Checklist</b> name it and give you the two ways to finish it by hand.</li>' +
      '</ul>' +
      '<p>Every conversion is checked against the rules of the RDL file ' +
        'format, and scored into the verdict above. Where this machine has ' +
        'the VB compiler that SSRS itself uses, every expression this tool ' +
        'wrote is compiled through it as well, and Validation reports how ' +
        'many passed.</p>' +
    '</div>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">What it will not do</h3>' +
      '<ul>' +
        '<li>It does not connect to your database. Nothing you drop here is sent anywhere: the conversion runs on this computer.</li>' +
        '<li>It does not check the result against the real Oracle output, because it never sees one. Compare the <b>Preview</b> against a known-good run yourself before you trust it.</li>' +
        '<li>It does not guess. When it cannot translate something, it leaves it clearly empty and names it in <b>Validation</b> and <b>Extras</b>, with what a person has to supply.</li>' +
        '<li>It does not deploy for you. You upload the file, using the steps in <b>Deploy Checklist</b>.</li>' +
      '</ul>' +
    '</div>' +

    '<div class="howto-section">' +
      '<h3 class=\"howto-h\">Words used in this tool</h3>' +
      '<table class="howto-views">' +
      '<caption class="sr-only">Glossary of terms used in Oracle2SSRS</caption>' +
      '<thead><tr><th scope="col">Word</th><th scope="col">What it means</th></tr></thead><tbody>' +
      '<tr><th scope="row"><b>SSRS</b></th><td>SQL Server Reporting Services. Microsoft&rsquo;s reporting server &mdash; where the finished report lives and runs.</td></tr>' +
      '<tr><th scope="row"><b>RDL</b></th><td>Report Definition Language. The file format SSRS reads. It is the file this tool gives you, and it ends in <code>.rdl</code>.</td></tr>' +
      '<tr><th scope="row"><b>Report Builder</b></th><td>Microsoft&rsquo;s editor for RDL files. You can open the file in it, but you do not have to.</td></tr>' +
      '<tr><th scope="row"><b>Refresh Fields</b></th><td>A button in Report Builder that rebuilds a query&rsquo;s field list by running the query. <b>Never click it here.</b> The field list is already complete, and refreshing makes Report Builder ask you for every parameter.</td></tr>' +
      '<tr><th scope="row"><b>Data source</b></th><td>The saved database connection. A <b>shared</b> data source is one that already exists on your report server and can be reused by many reports.</td></tr>' +
      '<tr><th scope="row"><b>Parameter</b></th><td>A value the report asks for before it runs, such as a date range or a county.</td></tr>' +
      '<tr><th scope="row"><b>Lexical</b></th><td>An Oracle trick: a named piece of SQL text that the report drops into its own query while it runs, usually to add a filter. This tool turns them into a real SSRS parameter wherever it can, and names the ones it cannot.</td></tr>' +
      '<tr><th scope="row"><b>Sub-report</b></th><td>A second report that a row in the first one opens. Also called a drill-through link.</td></tr>' +
      '<tr><th scope="row"><b>Bursting</b></th><td>Running a report once and sending each person only their own pages, as their own PDF.</td></tr>' +
      '<tr><th scope="row"><b>Fidelity</b></th><td>The percentage of the original that came across: columns and parameters carried into the new file, and layout columns the new file actually prints.</td></tr>' +
      '<tr><th scope="row"><b>T-SQL</b></th><td>Microsoft&rsquo;s version of SQL. Only used if you set the database on the left to SQL Server.</td></tr>' +
      '<tr><th scope="row"><b>Burst pack</b></th><td>A zip file containing everything needed to run bursting on your server: the script, its settings, and the setup notes.</td></tr>' +
      '</tbody></table>' +
    '</div>'
  );
}
// Everything focusable INSIDE a container, in tab order. Shared by the
// modal trap and the tour; `offsetParent` filters out anything the CSS has
// hidden, which a plain querySelectorAll would happily hand back.
const FOCUSABLE_SEL =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),' +
  'textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])';
function focusablesIn(root) {
  if (!root) return [];
  return $$(FOCUSABLE_SEL, root).filter(
    n => n.offsetParent !== null || n === document.activeElement);
}

// Trap Tab inside `panel` until `isOpen()` says the thing closed. A dialog
// that does not trap focus is a dialog you can Tab straight out of and into
// the page behind it -- which is what this one did: aria-modal="true" was
// set while 23 controls behind it stayed reachable.
function trapFocusWithin(panel, isOpen) {
  const onKey = (e) => {
    if (e.key !== "Tab" || !isOpen()) return;
    const items = focusablesIn(panel);
    if (!items.length) { e.preventDefault(); panel.focus(); return; }
    const first = items[0], last = items[items.length - 1];
    const here = document.activeElement;
    if (e.shiftKey && (here === first || here === panel || !panel.contains(here))) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && here === last) {
      e.preventDefault(); first.focus();
    }
  };
  document.addEventListener("keydown", onKey, true);
  return () => document.removeEventListener("keydown", onKey, true);
}

// Make the rest of the page unreachable while a modal is open. `inert` is
// the one attribute that removes a subtree from BOTH the tab order and the
// accessibility tree; aria-hidden is the fallback where inert is missing.
function setBackgroundInert(on) {
  [".layout", ".topbar"].forEach(sel => {
    const node = $(sel);
    if (!node) return;
    if (on) { node.setAttribute("inert", ""); node.setAttribute("aria-hidden", "true"); }
    else { node.removeAttribute("inert"); node.removeAttribute("aria-hidden"); }
  });
}

function initHowto() {
  const modal = document.getElementById("howto-modal");
  const body = document.getElementById("howto-body");
  const openBtn = document.getElementById("howto-open");
  const closeBtn = document.getElementById("howto-close");
  const backdrop = document.getElementById("howto-backdrop");
  const panel = document.getElementById("howto-panel");
  if (!modal || !body || !openBtn) return;
  let release = null;         // undo the focus trap
  let returnTo = null;        // the control that opened us
  const open = () => {
    returnTo = document.activeElement;
    body.innerHTML = _appHowtoHTML();
    modal.hidden = false;
    setBackgroundInert(true);
    // Focus goes INTO the dialog, not left behind on the button that
    // opened it -- otherwise Tab walks the page underneath.
    const first = focusablesIn(panel)[0];
    (first || panel).focus();
    release = trapFocusWithin(panel, () => !modal.hidden);
    const tourBtn = document.getElementById("howto-tour-start");
    if (tourBtn) tourBtn.addEventListener("click", () => {
      close();
      if (typeof window.o2sRunTour === "function") window.o2sRunTour();
    });
  };
  const close = () => {
    if (modal.hidden) return;
    modal.hidden = true;
    setBackgroundInert(false);
    if (release) { release(); release = null; }
    // ...and comes back to where it was, so the user does not restart at
    // the top of the document every time they read the help.
    if (returnTo && returnTo.focus) returnTo.focus();
    returnTo = null;
  };
  openBtn.addEventListener("click", open);
  if (closeBtn) closeBtn.addEventListener("click", close);
  if (backdrop) backdrop.addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.hidden) close(); });
  window.o2sCloseHowto = close;
}

// Append all deployment fields to a FormData (used by every convert call).
function appendDeployFields(fd) {
  const cs = getConnString(); if (cs) fd.append("connection_string", cs);
  const dsp = getSharedDsPath(); if (dsp) fd.append("shared_ds_path", dsp);
  const rsu = getReportServerUrl(); if (rsu) fd.append("report_server_url", rsu);
  const gal = getGenerateAllLabel(); if (gal) fd.append("generate_all_label", gal);
}

// ----- Report labels (generic override facility) -----
// The converter inventories every LITERAL label it emitted in

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
    // The state used to be a bare tick or dash with the WORDS only in a
    // title= tooltip -- unreachable by keyboard, invisible on touch, and
    // announced to nobody. Glyph AND word, both on screen: never colour or
    // an icon alone.
    const status = document.createElement("span");
    status.textContent = hasData ? "✓ present" : "— none yet";
    status.style.cssText = "font-size:11.5px;white-space:nowrap;color:" +
      (hasData ? "var(--good)" : "var(--ink-3)") + ";";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    // One of these per placeholder: without a name they are all announced
    // as "Choose file" and nothing says WHICH image they replace.
    input.setAttribute("aria-label", "Upload an image for " + label);
    // The class carries the 24px minimum target height (WCAG 2.2, 2.5.8).
    // Inline styles used to set only max-width and an 11px type size, which
    // measured 150x18 -- under the minimum, and only on reports that HAVE
    // image placeholders, which is how it stayed invisible.
    input.className = "image-slot-input";
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
      // "Pick .xml files first." said nothing about whether anything ran.
      // Same vocabulary as every other status line: glyph, word, sentence.
      setInlineStatus(status, "warn", "Nothing selected yet. Choose the "
        + "Oracle .xml exports above, then press this button again.");
      return;
    }
    // A batch is many reports at once -- the easiest way to cross the cap.
    const batchTooBig = uploadTooLargeMessage(files);
    if (batchTooBig) {
      setInlineStatus(status, "failed", batchTooBig);
      statusFail("The batch is too big to send", { why: batchTooBig,
        subject: files.length + " files", kind: "upload_too_large" });
      toast(batchTooBig, "err");
      return;
    }
    const batchSubject = files.length + " report"
      + (files.length === 1 ? "" : "s");
    const batchJob = "Converting " + batchSubject + " and scoring the migration";
    const batchBytes = estimateUploadBytes(files);
    setInlineStatus(status, "working", "Converting " + files.length
      + " report(s)… this stays on screen until it finishes.");
    statusBegin(batchSubject, batchJob,
                { label: "Sending " + formatBytes(batchBytes),
                  value: 0, max: batchBytes });
    if (resBox) resBox.innerHTML = "";
    const fd = new FormData();
    files.forEach(f => fd.append("files", f, f.name));
    appendDeployFields(fd);
    fd.append("target_db", getTargetDb());
    const chk = document.getElementById("batch-render");
    if (chk && chk.checked) fd.append("render", "1");
    try {
      const res = await postFormOrExplain("/api/batch", fd, batchBytes,
                                          uploadProgressReporter(batchJob, batchBytes));
      const j = await safeJson(res);
      if (!res.ok || j.error) throw apiError(j, res, "batch failed");
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
      setInlineStatus(status, "done", msg);
      if (resBox) {
        const a = document.createElement("a");
        a.href = "/api/download/batch-pack";
        a.textContent = "⬇ Download migration pack (RDLs + Assessment)";
        a.style.cssText = "display:block;margin:6px 0;font-size:12px;font-weight:600;";
        resBox.appendChild(a);
      }
      statusDone("Batch finished — " + msg, { subject: batchSubject,
        lines: ["Do this next: use the download link in the sidebar to get "
                + "the migration pack (every .rdl plus the assessment)."] });
      toast("Batch done — assessment ready", "ok");
    } catch (err) {
      const why = (err && err.message) || String(err);
      setInlineStatus(status, "failed", why);
      statusFail("The batch conversion stopped", { why: why,
        subject: batchSubject, kind: errorKind(err),
        next: "Nothing was saved. Try again with fewer files to find the one "
            + "that fails, then convert that one on its own to see its "
            + "findings." });
      toast(why, "err");
    }
  });
}

async function uploadReportImage(slot, file) {
  const tooBigImg = uploadTooLargeMessage([file]);
  if (tooBigImg) {
    statusFail("That image is too big to send", { why: tooBigImg,
      subject: file.name, kind: "upload_too_large" });
    setStatus("Upload too large", "err", tooBigImg);
    toast(tooBigImg, "err");
    return;
  }
  const imgBytes = estimateUploadBytes([file]);
  const imgJob = "Putting the image into the report";
  statusBegin(file.name, imgJob,
              { label: "Sending " + formatBytes(imgBytes), value: 0, max: imgBytes });
  setStatus("Embedding image…", "busy");
  const fd = new FormData();
  fd.append("slot", slot);
  fd.append("image", file, file.name);
  try {
    const res = await postFormOrExplain("/api/report-images/upload", fd,
                                        imgBytes,
                                        uploadProgressReporter(imgJob, imgBytes));
    const json = await safeJson(res);
    if (!res.ok || json.error) throw apiError(json, res, "image upload failed");
    if (json.rdl_xml) { onConverted(json); toast("Image embedded into the RDL + mockup", "ok"); }
    else { setStatus("Image stored", "ok"); toast(json.note || "Image stored", "ok"); }
  } catch (err) {
    console.error("[Oracle2SSRS] image upload failed:", err);
    const msg = (err && err.message) || "Image upload failed";
    statusFail("Could not add the image " + file.name,
               { why: msg, subject: file.name,
                 kind: errorKind(err) || "image_rejected",
                 next: "Check the file is a PNG, GIF or JPG and try again. "
                     + "The report itself was not changed." });
    setStatus("Error", "err", msg);
    toast(msg, "err");
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

// ----- Upload relevance filter + size pre-flight -----
//
// THE BUG THIS EXISTS FOR: a real artifact folder is mostly REFERENCE
// material. Dropping one used to append every single file to the FormData,
// so a folder whose only convertible input was a ~270 KB Oracle XML shipped
// ~36 MB across the wire because a rendered truth PDF rode along. That blows
// the server's MAX_CONTENT_LENGTH, and the rejection lands as a network-level
// fetch failure ("Failed to fetch") rather than a readable error, because the
// server answers before the upload has finished streaming.
//
// The partition below is STRUCTURAL -- extension plus size, never a filename
// pattern. Three tiers, each justified by what the server's bundle ingest
// actually does with the bytes:
//
//   INPUT    the ingest parses these for report structure, SQL or prose.
//            Their whole content matters, so size is never a reason to drop
//            one (Oracle XML exports genuinely run to several MB).
//   CONTEXT  the ingest samples these for a fixed-size signal only -- a PDF
//            contributes a page count to the cross-check, an image becomes a
//            screenshot/asset thumbnail. That contribution does not grow with
//            the file, so past a ceiling the bytes buy nothing and a big one
//            is truth/reference material, not an input.
//   OTHER    an extension the ingest cannot read at all. Tiny strays still go
//            up so the Ingest Summary can list them honestly; large ones are
//            pure payload and are dropped.
const UPLOAD_INPUT_EXTS = [
  "xml", "rdf", "jsp", "sql", "docx", "txt", "md", "rst",
];
const UPLOAD_CONTEXT_EXTS = [
  "pdf", "png", "jpg", "jpeg", "gif", "bmp", "webp",
];
// A report asset (seal, logo, header strip) and a useful screenshot are well
// under this; a rendered report PDF is far over it.
const UPLOAD_CONTEXT_MAX_BYTES = 2 * 1024 * 1024;
// Unreadable extensions: keep only what costs nothing to carry.
const UPLOAD_OTHER_MAX_BYTES = 1 * 1024 * 1024;

function uploadExt(name) {
  const base = String(name || "").split(/[\\/]/).pop();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
}

function formatBytes(n) {
  const b = Number(n) || 0;
  if (b >= 1024 * 1024) return (b / (1024 * 1024)).toFixed(1) + " MB";
  if (b >= 1024) return Math.round(b / 1024) + " KB";
  return b + " B";
}

// Partition a dropped file list into what gets uploaded and what is skipped.
// Returns {send: [File], skipped: [{file, name, size, reason}], skippedBytes,
//          sendBytes}. Pure and dependency-free so it is directly testable.
function partitionUploadList(list) {
  const send = [];
  const skipped = [];
  let sendBytes = 0;
  let skippedBytes = 0;
  (list || []).forEach(f => {
    const name = (f && (f._relPath || f.name)) || "";
    const size = (f && Number(f.size)) || 0;
    const ext = uploadExt(name);
    let reason = null;
    if (UPLOAD_INPUT_EXTS.indexOf(ext) >= 0) {
      reason = null;                       // always an input
    } else if (UPLOAD_CONTEXT_EXTS.indexOf(ext) >= 0) {
      if (size > UPLOAD_CONTEXT_MAX_BYTES) {
        reason = (ext === "pdf")
          ? "report PDFs aren't conversion inputs"
          : "oversized image, not a report asset";
      }
    } else if (size > UPLOAD_OTHER_MAX_BYTES) {
      reason = "this kind of file cannot be read";
    }
    if (reason) {
      skipped.push({ file: f, name: name, size: size, reason: reason });
      skippedBytes += size;
    } else {
      send.push(f);
      sendBytes += size;
    }
  });
  return { send: send, skipped: skipped, sendBytes: sendBytes, skippedBytes: skippedBytes };
}

// One short line explaining what was left behind, e.g.
// "Skipped 3 reference files (38.2 MB): report PDFs aren't conversion inputs".
function describeSkipped(skipped, skippedBytes) {
  if (!skipped || !skipped.length) return "";
  const reasons = [];
  skipped.forEach(s => { if (reasons.indexOf(s.reason) < 0) reasons.push(s.reason); });
  const noun = skipped.length === 1 ? "reference file" : "reference files";
  return "Skipped " + skipped.length + " " + noun +
         " (" + formatBytes(skippedBytes) + "): " + reasons.join("; ");
}

// The server's live MAX_CONTENT_LENGTH, rendered into the page by the index
// route. Never a literal in this file -- if the deployment raises or lowers
// O2S_MAX_UPLOAD_MB the client follows automatically.
function maxUploadBytes() {
  if (typeof state === "object" && state && state._maxUploadBytes > 0) {
    return state._maxUploadBytes;
  }
  const attr = document.body && document.body.getAttribute("data-max-upload-bytes");
  const n = Number(attr);
  return (isFinite(n) && n > 0) ? n : 0;
}

// Refresh the cap from /api/health. Doubles as the reachability probe below.
async function refreshUploadLimit() {
  const res = await fetch("/api/health", { method: "GET", cache: "no-store" });
  const json = await res.json();
  const n = Number(json && json.max_upload_bytes);
  if (isFinite(n) && n > 0 && typeof state === "object" && state) {
    state._maxUploadBytes = n;
  }
  return json;
}

// Multipart framing overhead: per-part headers plus the boundary. A folder of
// many small files is measurably bigger on the wire than the sum of its file
// sizes, so the pre-flight budgets for it instead of squeaking under the cap
// and then getting rejected anyway.
const UPLOAD_PART_OVERHEAD_BYTES = 320;

function estimateUploadBytes(files) {
  return (files || []).reduce(
    (n, f) => n + (Number(f && f.size) || 0) + UPLOAD_PART_OVERHEAD_BYTES, 2048);
}

// Pre-flight: would this payload be rejected? Returns null when it fits, or a
// ready-to-show message naming the largest offenders and the actual cap.
function uploadTooLargeMessage(files) {
  const cap = maxUploadBytes();
  if (!cap) return null;                        // cap unknown -- don't guess
  const total = estimateUploadBytes(files);
  if (total <= cap) return null;
  const biggest = (files || []).slice()
    .sort((a, b) => (Number(b && b.size) || 0) - (Number(a && a.size) || 0))
    .slice(0, 3)
    .map(f => ((f && (f._relPath || f.name)) || "?").split(/[\\/]/).pop() +
              " (" + formatBytes(f && f.size) + ")");
  return "Upload is " + formatBytes(total) + " but this server accepts at most " +
         formatBytes(cap) + " per request. Largest: " + biggest.join(", ") +
         ". Drop just the Oracle XML export, plus any SQL or Word files that " +
         "go with it. Or " +
         "raise the cap with the O2S_MAX_UPLOAD_MB environment variable.";
}

// ----- API calls -----

// Is the server actually up? A fetch that rejects at the network level looks
// identical whether the server died or the request was torn down mid-upload,
// so ASK before blaming either. Short timeout: this runs on the error path.
async function serverReachable() {
  try {
    const ctl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), 4000) : null;
    const res = await fetch("/api/health", {
      method: "GET", cache: "no-store", signal: ctl ? ctl.signal : undefined });
    if (t) clearTimeout(t);
    return !!(res && res.ok);
  } catch (_) {
    return false;
  }
}

// Turn a raw fetch rejection into something the user can act on.
//
// A bare "Failed to fetch" is what the browser reports for BOTH "the server
// isn't running" and "the request was aborted mid-upload" -- and it was the
// entire user-visible content of this bug's failure toast. Probe the server
// and say which one it is.
// Returns {message, kind}. The KIND is the point: the probe below is the
// only place that can tell these three apart, so it says which one it found
// in a code the status surface can act on, instead of leaving the next step
// to be guessed from the sentence afterwards.
async function explainNetworkFailure(err, payloadBytes) {
  const up = await serverReachable();
  if (!up) {
    return { kind: "server_unreachable",
      message: "Can't reach the converter — is the server still running? " +
           "Restart it with run.bat, then try again." };
  }
  const cap = maxUploadBytes();
  if (payloadBytes && cap && payloadBytes > cap) {
    return { kind: "upload_too_large",
      message: "Upload rejected: " + formatBytes(payloadBytes) +
           " exceeds this server's " + formatBytes(cap) + " limit. " +
           "Drop just the Oracle XML export, or raise O2S_MAX_UPLOAD_MB." };
  }
  return { kind: "upload_interrupted",
    message: "The upload was cut off before the server could answer" +
         (payloadBytes ? " (" + formatBytes(payloadBytes) + " sent)" : "") +
         ". The server is up, so this is usually an oversized or interrupted " +
         "request — try dropping fewer files." };
}

// Real, measured upload progress. fetch() cannot report how much of a body
// has gone out; XMLHttpRequest can, and the upload leg is the part of a big
// folder drop that actually takes time (a real artifact folder runs to
// megabytes). So this is the ONE place the app can honestly show a
// percentage -- everything after it is one server call that answers when it
// is done, and that stays indeterminate rather than pretending.
//
// The resolved object is Response-shaped for the two things every caller
// uses (`.ok`/`.status` and `await res.json()`), so safeJson() and the
// error paths behave exactly as they do on the fetch path.
function _xhrPostForm(url, fd, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    if (xhr.upload) {
      xhr.upload.addEventListener("progress", (e) => {
        try {
          if (e.lengthComputable) onProgress(e.loaded, e.total);
        } catch (_) { /* a broken status render must never kill an upload */ }
      });
      // Bytes are all gone; from here the server is working and nobody
      // knows the fraction. Signalled as "no numbers".
      xhr.upload.addEventListener("load", () => {
        try { onProgress(null, null); } catch (_) {}
      });
    }
    xhr.addEventListener("load", () => {
      const text = xhr.responseText || "";
      resolve({
        ok: xhr.status >= 200 && xhr.status < 300,
        status: xhr.status,
        statusText: xhr.statusText || "",
        json: () => Promise.resolve(JSON.parse(text)),
        text: () => Promise.resolve(text),
      });
    });
    // Same shape of rejection fetch() gives, so explainNetworkFailure()
    // below tells the user the same true story either way.
    xhr.addEventListener("error", () => reject(new TypeError("Failed to fetch")));
    xhr.addEventListener("abort", () => reject(new TypeError("Failed to fetch")));
    xhr.addEventListener("timeout", () => reject(new TypeError("Failed to fetch")));
    xhr.send(fd);
  });
}

// Every upload POST goes through here so the network-level rejection can
// never surface as a raw "Failed to fetch" again.
async function postFormOrExplain(url, fd, payloadBytes, onProgress) {
  try {
    if (fd && typeof onProgress === "function"
        && typeof XMLHttpRequest !== "undefined") {
      return await _xhrPostForm(url, fd, onProgress);
    }
    return await fetch(url, { method: "POST", body: fd });
  } catch (err) {
    const why = await explainNetworkFailure(err, payloadBytes);
    const wrapped = new Error(why.message);
    wrapped.networkFailure = true;
    wrapped.kind = why.kind;
    wrapped.cause = err;
    throw wrapped;
  }
}

// Parse a fetch Response body as JSON without ever throwing. The server
// answers errors in JSON ({error: ...}, incl. 413 uploads-too-large), but a
// proxy or stock server error page can still be raw HTML -- a bare
// res.json() then throws a SyntaxError and the user sees a generic toast
// instead of the actual reason. Falls back to an honest per-status message.
async function safeJson(res) {
  try { return await res.json(); }
  catch (_) {
    if (res.status === 413) {
      return { error: "Upload too large — the server rejected it before " +
                      "conversion. Only the Oracle Reports XML export is " +
                      "needed; truth PDFs/screenshots don't have to be uploaded.",
               error_kind: "upload_too_large" };
    }
    return { error: "HTTP " + res.status + (res.statusText ? " " + res.statusText : ""),
             error_kind: (res.status >= 500 ? "server_error" : "invalid_request") };
  }
}

// One Error for every API answer that is not OK, carrying the server's own
// structural `error_kind` so the status surface never has to read the prose.
// A server that predates the field still yields a usable kind from the HTTP
// status, and an unknown kind degrades to the honest generic next step.
function apiError(json, res, fallback) {
  const e = new Error((json && json.error) || fallback || "the step failed");
  e.kind = (json && json.error_kind)
    || (res && res.status >= 500 ? "server_error"
        : (res && res.status === 413 ? "upload_too_large"
           : (res && res.status >= 400 ? "invalid_request" : "")));
  return e;
}

// The kind carried by whatever was thrown, if it carried one at all.
function errorKind(err) {
  return (err && typeof err.kind === "string") ? err.kind : "";
}

// One progress reporter for every upload path. Two honest phases:
//   * while the body is going out we know the exact byte count, so the bar
//     is DETERMINATE and the label says how far it has got;
//   * once the last byte is sent the server is working and no one knows the
//     fraction, so the bar goes indeterminate and SAYS so.
function uploadProgressReporter(headline, totalBytes) {
  return function (loaded, total) {
    if (loaded == null) {
      statusProgress(headline,
        "Upload finished — the converter is reading the file now",
        null, null);
      return;
    }
    const t = total || totalBytes || 0;
    statusProgress(headline,
      "Sending " + formatBytes(loaded) + " of " + formatBytes(t),
      loaded, t);
  };
}

// WHAT ACTUALLY CAME ACROSS, counted -- the fact the headline is not
// allowed to contradict.
//
// THE DEFECT THIS EXISTS FOR, measured on the real page. A TRUNCATED Oracle
// export (the first 700 bytes of a real one) converted, and the top of the
// screen said "Converted <that report's name>" with the corner pill reading
// the same, while the line underneath admitted "0 queries, 0 parameters and
// 0 formulas came across". Nothing was hidden -- the counts were right there
// -- but the HEADLINE, the one thing a reader takes away at a glance,
// claimed a success that had not happened. The counts now decide the
// headline, so the two cannot disagree.
//
//   "empty" -- nothing at all was extracted. The .rdl is a shell.
//   "thin"  -- something came across, but one of the three things a report
//              cannot work without is missing entirely: a query to read, a
//              column to read, or one place on the page bound to the data.
//   ""      -- a real report came across.
//
// The three "thin" tests are ALL-OR-NOTHING on purpose, and they were
// measured before being trusted: over the 32 complete reports on this
// machine (every real artifact folder, the bundled samples and the test
// fixtures) not one has zero queries, zero source columns or zero bound
// layout fields. So this cannot cry wolf on a report that really converted;
// it only fires on an extraction that produced no report at all.
//
// The column and field counts come from the fidelity report, which is built
// inside a try/except and can come back empty (or, from a folder drop, not
// at all). A COUNT THAT WAS NEVER TAKEN IS NOT A COUNT OF ZERO: when the
// measurement is missing, this falls back to what the report itself says and
// never accuses a healthy conversion of being empty on the strength of a
// crash somewhere else.
function _fidelityCounts(data) {
  const fid = (data && data.fidelity_report) || {};
  const cats = fid.categories || {};
  const measured = !fid.error && !!cats.columns && !!cats.layout_fields;
  return {
    measured: measured,
    cols: ((cats.columns || {}).total) || 0,
    fields: ((cats.layout_fields || {}).total) || 0,
  };
}

function extractionShortfall(data) {
  const r = (data && data.report) || {};
  const q = (r.queries || []).length;
  const p = (r.parameters || []).length;
  const f = (r.formulas || []).length;
  const m = _fidelityCounts(data);
  if (!q && !p && !f && (!m.measured || (!m.cols && !m.fields))) return "empty";
  if (!q) return "thin";
  if (m.measured && (!m.cols || !m.fields)) return "thin";
  return "";
}

// Which of the three is missing, said in the operator's terms. Named from
// the counts, so it cannot drift away from what the card is showing.
function shortfallReason(data) {
  const r = (data && data.report) || {};
  const m = _fidelityCounts(data);
  if (!(r.queries || []).length) {
    return "There is no query, so the report has nothing to read. It will "
         + "print its headings and stop.";
  }
  if (!m.cols) {
    return "No column came across, so the queries have nothing to select and "
         + "the page has nothing to show.";
  }
  return "Nothing on the page is tied to the data: every box the report "
       + "would print is empty.";
}

// Does the Oracle export itself stop in the middle? A complete Oracle
// Reports export ends with its closing </report> tag; a file cut short by a
// half-finished download, a truncated copy or a size-capped export does not.
// Checked, never guessed -- the sentence is only ever printed when the tag
// really is missing from the bytes the server parsed.
function sourceStopsMidFile(data) {
  const src = (data && data.oracle_xml) || "";
  if (!src) return false;
  return src.slice(-4000).toLowerCase().indexOf("</report>") === -1;
}

// ---- ONE verdict for one conversion -------------------------------------
//
// THE DEFECT THIS EXISTS FOR, measured on a real report (ASBST_ACTIVITY_FEE,
// page defaults, nothing changed). Four surfaces described the same
// conversion at the same moment:
//
//   sidebar banner   "✓ Ready to upload — All 89 calculations in this report
//                     compiled successfully. Nothing is standing in your way."
//   deploy strip     "Ready — Ready to upload"
//   status card      "Do this next: download the .rdl file and upload it"
//   deploy checklist "Read the Oracle SQL the converter wrote (4 errors, 6
//                     warnings)" ... "SSRS will fail to bind it."
//
// Nothing was hidden -- the errors were on the Validation view and in the
// checklist. But three of the four surfaces decided "is this deployable?"
// from the PREFLIGHT list alone, while the checklist decided it from the
// VALIDATION list, and the two lists disagree. A reader who cannot ask a
// colleague which surface to believe is left with a green light over four
// errors that say the report will not bind.
//
// So the verdict is decided here, once, from BOTH lists -- the same two the
// deploy checklist's SQL step counts (_deploy_step_sql in backend/app.py) --
// and every surface renders whatever this returns. A disagreement between
// them is no longer unlikely; it is unspellable.
//
//   key          "blocker" | "runtime" | "ready"   (the data-verdict values)
//   blockers     preflight BLOCKERs: it will not load at all
//   runtime      preflight REDs + validation/RDL errors: it loads, and
//                something goes wrong when it runs
//   needsHuman   the checklist's OWN unfinished steps, by title, so the
//                banner names what still needs a person instead of claiming
//                nothing does
function conversionVerdict(data) {
  data = data || {};
  const up = s => String(s || "").toUpperCase();
  const low = s => String(s || "").toLowerCase();
  const pfIssues = ((data.preflight || {}).issues) || [];
  const blockers = pfIssues.filter(i => up(i.severity) === "BLOCKER").length;
  const reds = pfIssues.filter(i => up(i.severity) === "RED").length;
  const sqlErrors = (data.validation_issues || [])
    .concat(data.rdl_issues || [])
    .filter(i => low(i.severity) === "error").length;
  const needsHuman = (data.deployment_checklist || [])
    .filter(s => s && (s.status === "todo" || s.status === "caution"))
    .map(s => String(s.title || "").trim())
    .filter(Boolean);
  const runtime = reds + sqlErrors;
  return {
    key: blockers ? "blocker" : (runtime ? "runtime" : "ready"),
    blockers: blockers, reds: reds, sqlErrors: sqlErrors, runtime: runtime,
    needsHuman: needsHuman,
  };
}

// What a finished conversion means, in words, with the next step. This is
// the "durable record of what happened last" the status strip owes the
// user: which report, what came across, and whether it can be uploaded.
function reportConversionStatus(data) {
  const r = (data && data.report) || {};
  const pf = (data && data.preflight) || {};
  const name = r.name || "the report";
  const v = conversionVerdict(data);
  const blockers = v.blockers, reds = v.runtime;
  const lines = [(r.queries || []).length + " quer"
    + ((r.queries || []).length === 1 ? "y" : "ies") + ", "
    + (r.parameters || []).length + " parameter"
    + ((r.parameters || []).length === 1 ? "" : "s") + " and "
    + (r.formulas || []).length + " formula"
    + ((r.formulas || []).length === 1 ? "" : "s") + " came across."];
  let stateName = "done";
  // The headline says what came across, not that something happened. An
  // empty or near-empty extraction says so HERE, at the top, in the place
  // the success would otherwise have been.
  const shortfall = extractionShortfall(data);
  let headline = "Converted " + name;
  if (shortfall === "empty") {
    stateName = "warn";
    headline = "Nothing came across from " + name;
    lines.push("The file that was written is an empty shell: it will open in "
      + "Report Builder and print nothing. Do not upload it.");
  } else if (shortfall === "thin") {
    stateName = "warn";
    headline = "Almost nothing came across from " + name;
    lines.push(shortfallReason(data));
  }
  if (shortfall && sourceStopsMidFile(data)) {
    lines.push("Why: the Oracle export itself stops in the middle — it never "
      + "reaches its closing </report> tag, so most of the report was never "
      + "in the file that was dropped.");
  }
  if (shortfall) {
    lines.push("Do this next: export the report again from Oracle Reports, "
      + "check the new file is complete, and drop that. Validation lists "
      + "what the converter could not find.");
  } else if (pf.source_kind) {
    stateName = "warn";
    lines.push("Do this next: this file is only part of an Oracle report, "
      + "not a whole one. Export the complete report from Oracle Reports and "
      + "drop that instead. Validation says which part is missing.");
  } else if (blockers) {
    stateName = "warn";
    lines.push("Do this next: open the Validation view and fix "
      + blockers + " blocker" + (blockers > 1 ? "s" : "")
      + ". Until then the file will not load cleanly in Report Builder.");
  } else if (reds) {
    stateName = "warn";
    lines.push("Do this next: open the Validation view. The file will "
      + "upload, but " + reds + " thing" + (reds > 1 ? "s" : "")
      + " may go wrong when the report actually runs.");
  } else {
    lines.push("Do this next: download the .rdl file and upload it to "
      + "your report server. Do not click Refresh Fields in Report Builder — "
      + "the field list is already complete, and refreshing it makes Report "
      + "Builder ask you for every parameter.");
  }
  statusDone(headline, { state: stateName, subject: name, lines: lines });
}

async function uploadFile(file) {
  // Single-file drops get the same pre-flight: an Oracle XML export is small,
  // but nothing stops a user dropping something huge onto the same zone.
  const tooBig = uploadTooLargeMessage([file]);
  if (tooBig) {
    console.error("[Oracle2SSRS] upload blocked before sending: " + tooBig);
    statusFail("That file is too big to send", { why: tooBig,
      subject: file.name, kind: "upload_too_large" });
    setStatus("Upload too large", "err", tooBig);
    toast(tooBig, "err");
    return;
  }
  const bytes = estimateUploadBytes([file]);
  const job = "Converting to an SSRS report";
  statusBegin(file.name, job,
              { label: "Sending " + formatBytes(bytes), value: 0, max: bytes });
  setStatus("Converting…", "busy");
  state.lastFile = file; state.lastBundle = null;
  const fd = new FormData();
  fd.append("file", file);
  appendDeployFields(fd);
  fd.append("target_db", getTargetDb());
  try {
    const res = await postFormOrExplain("/api/convert", fd, bytes,
                                        uploadProgressReporter(job, bytes));
    const json = await safeJson(res);
    if (!res.ok || json.error) throw apiError(json, res, "Conversion failed");
    onConverted(json);
  } catch (err) {
    console.error("[Oracle2SSRS] convert failed:", err);
    const msg = (err && err.message) || "Failed to convert";
    statusFail("Could not convert " + file.name,
               { why: msg, subject: file.name, kind: errorKind(err) });
    setStatus("Error", "err", msg);
    toast(msg, "err");
  }
}

async function uploadBundle(list) {
  // LAYER 1 -- relevance filter. Only files the conversion can actually read
  // go on the wire; reference material stays on disk and is reported.
  const part = partitionUploadList(list);
  const skipLine = describeSkipped(part.skipped, part.skippedBytes);
  if (skipLine) {
    console.log("[Oracle2SSRS] " + skipLine + " — " +
                part.skipped.map(s => s.name + " (" + formatBytes(s.size) + ")").join(", "));
    toast(skipLine, "ok");
  }
  if (!part.send.length) {
    const why = skipLine || "Nothing in that folder is a conversion input.";
    // NOT no_report_in_drop: that kind's advice ends "the Ingest Summary
    // below lists what each file was taken to be", and nothing was sent, so
    // no Ingest Summary exists to read. The kind says WHERE this failed.
    statusFail("Nothing in that drop can be converted", { why: why,
      kind: "reference_files_only" });
    setStatus("Nothing to convert", "err", why);
    toast(why, "err");
    return;
  }

  // LAYER 2 -- size pre-flight. Never fire a request the server is certain to
  // reject; a doomed POST is exactly what produced the unreadable failure.
  const payloadBytes = estimateUploadBytes(part.send);
  const tooBig = uploadTooLargeMessage(part.send);
  if (tooBig) {
    console.error("[Oracle2SSRS] upload blocked before sending: " + tooBig);
    statusFail("That drop is too big to send", { why: tooBig,
      subject: part.send.length + " file"
        + (part.send.length === 1 ? "" : "s"),
      kind: "upload_too_large" });
    setStatus("Upload too large", "err", tooBig);
    toast(tooBig, "err");
    return;
  }

  const subject = part.send.length + " file"
    + (part.send.length === 1 ? "" : "s");
  const job = "Reading the files you dropped and building the report";
  statusBegin(subject, job,
              { label: "Sending " + formatBytes(payloadBytes),
                value: 0, max: payloadBytes });
  setStatus("Reading " + part.send.length + " file(s)…", "busy");
  state.lastBundle = part.send; state.lastFile = null;
  const fd = new FormData();
  part.send.forEach(f => fd.append("files", f, f._relPath || f.name));
  appendDeployFields(fd);
  fd.append("target_db", getTargetDb());
  try {
    const res = await postFormOrExplain("/api/convert-bundle", fd, payloadBytes,
                                        uploadProgressReporter(job, payloadBytes));
    const json = await safeJson(res);
    if (!res.ok) throw apiError(json, res, "Bundle conversion failed");
    if (json.error === "no_convertible_artifacts") {
      renderIngestSummary(json.ingest_report || {});
      // The same note the folder drop gets, so the instruction is in the
      // same place whatever shape the drop had.
      renderRdfGuidance(json);
      if (json.rdf_hint) {
        console.log("[Oracle2SSRS] .rdf detected:\n" + json.rdf_hint);
        // The command used to exist ONLY in the browser console. A report
        // writer cannot be told to open DevTools, so it is on screen and
        // selectable, in the failure card, next to the reason.
        statusFail("Those files cannot be converted yet — one of them is a "
                   + "compiled Oracle .rdf",
          { subject: subject, kind: json.error_kind || "rdf_binary",
            why: "A .rdf is Oracle's compiled binary. This tool reads the "
               + "XML export, which is a different file.",
            next: "In Oracle, run the command below to write the XML export, "
                + "then drop that .xml file here.",
            cmd: String(json.rdf_hint).trim() });
        toast(".rdf binary detected — the export command is in the Status "
              + "panel above", "err");
      } else {
        statusFail("Those files did not contain a report",
          { subject: subject, kind: json.error_kind || "no_report_in_drop",
            why: "Nothing in the drop was an Oracle Reports XML export or a "
               + "SQL file this tool can read.",
            next: "Add the report's Oracle XML export to the drop. The Ingest "
                + "Summary below lists what did arrive." });
        toast("Found files, but none of them make a report — see the list below", "err");
      }
      setStatus("Nothing to convert", "err",
                "There was no Oracle XML export and no SQL file in what you dropped. The list below says what each file was taken to be.");
      return;
    }
    if (json.error) throw apiError(json, res, "Bundle conversion failed");
    onConverted(json);
  } catch (err) {
    console.error("[Oracle2SSRS] bundle failed:", err);
    const msg = (err && err.message) || "Failed to ingest";
    statusFail("Could not build a report from those " + subject,
               { why: msg, subject: subject, kind: errorKind(err) });
    setStatus("Error", "err", msg);
    toast(msg, "err");
  }
}

async function runSample(name, btn) {
  // Re-entry guard: duplicate event wiring once fired one click as several
  // identical conversions, whose racing responses left the preview hung.
  // The guard makes that impossible no matter how the button gets wired.
  if (runSample._busy) return;
  runSample._busy = true;
  statusBegin(name, "Converting the bundled sample report");
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
    const res = await postFormOrExplain(
      "/api/convert-sample/" + encodeURIComponent(name) + _qs, undefined, 0);
    const json = await safeJson(res);
    if (!res.ok || json.error) throw apiError(json, res, "Sample failed");
    onConverted(json);
  } catch (err) {
    console.error("[Oracle2SSRS] sample failed:", err);
    const msg = (err && err.message) || "Failed to load sample";
    statusFail("Could not convert the sample " + name,
               { why: msg, subject: name, kind: errorKind(err) });
    setStatus("Error", "err", msg);
    toast(msg, "err");
  } finally {
    runSample._busy = false;
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
  // Return the promise so callers (and the tour) can await the upload.
  return uploadBundle(list);
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
  state._convSeq = (state._convSeq || 0) + 1;   // invalidates highlight cache
  // Fresh content: whatever failure was on screen is about the run before
  // this one, so it stops being "what you are looking at".
  statusClearAlert();
  // reportConversionStatus writes the card AND re-renders the corner pill
  // from it. A setStatus("Converted", "ok") used to fire here as well: it was
  // a no-op only while the card happened to contain the word "Converted", and
  // the moment the card started saying "Nothing came across from X" -- which
  // is the whole point of the counts deciding the headline -- that second
  // call would have overwritten the honest headline with the old claim. One
  // event, one status write.
  reportConversionStatus(data);
  if ($("#empty-state")) $("#empty-state").hidden = true;
  // Each renderer is isolated: onConverted used to be one straight-line
  // call sequence, so a single renderer throwing on an unusual report shape
  // aborted EVERYTHING after it — the sidebar filled in but every tab panel
  // stayed empty and activateTab never ran, i.e. "tabs don't load". One bad
  // card must never blank the whole app; log it loudly and keep going.
  [renderSummary, renderImageSlots, resetRenderState,
   // renderIngestSummary hides the panel's banner, so the .rdf note is
   // written after it, never before.
   (d) => { if (d.ingest_report) renderIngestSummary(d.ingest_report); },
   renderRdfGuidance,
   renderCrossValidation, renderEnrichmentBanner, renderMockupTab,
   renderRdlTab, renderSideBySideTab, renderLiveTab, renderValidationTab,
   renderDeploymentTab, renderExtrasTab, renderBurstingTab, renderSubreports,
   renderWarnings, renderPreflight, renderDeployStatus,
   showMockupCTA, pushRecent,
  ].forEach(fn => {
    try { fn(data); }
    catch (e) {
      console.error("[Oracle2SSRS] renderer failed (continuing):",
                    fn.name || "(anonymous)", e);
    }
  });
  // Show whatever tab was active (this also highlights just that panel;
  // the old Prism.highlightAll() here re-tokenized every block at once).
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
  // The per-report detail is ON-SCREEN text under the score. It used to be
  // frow.title -- a tooltip that only a mouse can open, that never shows on
  // touch, and that no keyboard user can reach or dismiss.
  const fdet = $("#sum-fidelity-detail");
  const showDetail = (txt) => {
    if (!fdet) return;
    fdet.textContent = txt || "";
    fdet.hidden = !txt;
  };
  const _srcKind = (data.preflight || {}).source_kind;
  if (fval && _srcKind) {
    // Partial Oracle artifact (customization overlay / data-model-only /
    // layout fragment): a coverage percentage over a non-report is
    // meaningless -- show an honest dash, never "100%".
    fval.textContent = "—";
    if (frow) frow.classList.remove("fidelity-full", "fidelity-partial");
    showDetail("Not applicable — this file is a partial Oracle export, not a "
      + "complete report (see Validation for details).");
  } else if (fval && fid && typeof fid.score === "number") {
    // Same honesty rule as the fidelity card: the headline is the WORST of
    // binding coverage and layout-display coverage, and it never reads
    // "full" while the deployment verdict is unhappy.
    const _lf = (fid.categories || {}).layout_fields || {};
    const _disp = (typeof _lf.display_coverage === "number")
      ? _lf.display_coverage : 1;
    const _verdict = (data.preflight || {}).verdict;
    const _blocked = _verdict === "BLOCKER" || _verdict === "RED";
    const _headline = Math.min(fid.score, _disp);
    const pct = Math.round(_headline * 100);
    const _full = (_headline >= 1 && !_blocked);
    // The number alone said nothing: 73% and 100% painted in exactly the
    // same ink, and the only difference was a class that styled nothing.
    // Glyph + word + number, so the state survives without colour.
    fval.textContent = "";
    fval.classList.add("inline-status");
    fval.setAttribute("data-state", _full ? "done" : "warn");
    fval.appendChild(el("span", { class: "inline-glyph", "aria-hidden": "true",
      text: statusState(_full ? "done" : "warn").glyph }));
    fval.appendChild(document.createTextNode(pct + "%"));
    fval.appendChild(el("span", { class: "inline-state",
      text: _full ? "complete" : "partial" }));
    if (frow) {
      frow.classList.remove("fidelity-full", "fidelity-partial");
      frow.classList.add(_full ? "fidelity-full" : "fidelity-partial");
    }
    showDetail(fid.summary || "");
  } else if (fval) {
    fval.textContent = "—";
    showDetail("");
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
      '<b>Cross-check</b> — ' +
      'what the Oracle XML says, against the SQL and Word files dropped with it. ' +
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


// The .rdf recovery instruction, on screen, in EVERY drop shape.
//
// THE DEFECT THIS EXISTS FOR, measured on the served page: a .rdf dropped
// ALONE produced the failure card carrying the rwconverter command, and a
// .rdf dropped inside its report's folder -- the everyday shape, and the one
// the drop zone invites -- produced a successful conversion and no mention
// of the .rdf at all. The operator who does the normal thing was left with
// no way to know that file was skipped, or what to do about it.
//
// It cannot be a failure card here: on the folder drop the conversion
// SUCCEEDED, and two contradicting verdicts about one drop is the other
// defect this session fixed. It is a note in the panel that already lists
// what each dropped file was taken to be, it names the file it applies to,
// and the command is selectable text.
function renderRdfGuidance(data) {
  const host = $("#ingest-banner");
  if (!host) return;
  const hint = (data && data.rdf_hint) || "";
  const file = ((data && data.ingest_report) || {}).rdf_binary || "";
  if (!hint) {                       // a fresh drop with no .rdf clears it
    host.innerHTML = "";
    host.hidden = true;
    return;
  }
  const named = file || "One file in this drop";
  const converted = !!(data && data.report);
  setInlineStatus(host, "warn",
    named + " was not converted: it is Oracle's compiled binary (.rdf), and "
    + "this tool reads the XML export."
    + (converted
       ? " The report on screen was built from the other files you dropped."
       : "")
    + " To convert " + (file ? "it" : "that file")
    + (converted ? " as well, run" : ", run")
    + " this where Oracle Reports is installed, then drop the "
    + ".xml it writes:");
  host.appendChild(el("code", { class: "activity-cmd", text: String(hint).trim() }));
  host.hidden = false;
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
        el("span", { class: "ingest-note",
                     text: c.note || `how sure this tool is: ${c.confidence ?? "not known"}` })
      ));
    });
  }
  wrap.hidden = false;
}

// ----- Tab 1: Mockup -----
// Two ways to look at the SAME report:
//   "frontend" -> the generated .rdl rendered by Microsoft's report engine
//                 (the REAL output). The browser mockup (data.mockup_html)
//                 paints instantly as a stand-in while the engine works --
//                 or permanently on machines without the engine.
//   "backend"  -> data.mockup_backend_html (Report Builder design skeleton)
const MOCKUP_MODES = ["frontend", "backend"];

// A fresh conversion invalidates any pages rendered for the previous one.
// The token guards against a slow render response landing AFTER the user
// converted a different report -- stale pages must never paint.
function resetRenderState() {
  state.renderedPages = null;
  state.renderedPdf = null;
  // The page descriptions belong to the pages they describe: a stale note
  // would put the previous report's opening line in this one's alt text.
  state.renderedNotes = null;
  state._renderFailed = null;
  state._renderToken = (state._renderToken || 0) + 1;
}

function _setMockupMode(mode) {
  state.mockupMode = mode;
  MOCKUP_MODES.forEach(m => {
    const b = document.getElementById("mockup-mode-" + m);
    if (!b) return;
    const on = m === mode;
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.classList.toggle("mockup-mode-active", on);
    b.tabIndex = on ? 0 : -1;
  });
  if (state.data) renderMockupTab(state.data);
}

// The render toolbar's own line. It is a live region because the render
// finishes on its own clock -- the user is looking at the preview, not at
// the sidebar, when the pages arrive -- and it is polite because a finished
// render is news, not an emergency.
function _renderStatus(msg, stateName) {
  const el = document.getElementById("render-status");
  if (!el) return;
  if (!el.getAttribute("role")) {
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
  }
  el.innerHTML = "";
  if (!msg) return;
  const st = statusState(stateName || "working");
  el.classList.add("inline-status");
  el.setAttribute("data-state", STATUS_STATES[stateName] ? stateName : "working");
  el.appendChild(el2("span", "inline-glyph", st.glyph, true));
  el.appendChild(el2("span", "inline-state", st.word, false));
  const body = document.createElement("span");
  body.className = "inline-text";
  body.innerHTML = msg;          // callers pass small trusted markup
  el.appendChild(body);
}
function el2(tag, cls, text, hidden) {
  const n = document.createElement(tag);
  n.className = cls;
  n.textContent = text;
  if (hidden) n.setAttribute("aria-hidden", "true");
  return n;
}

function renderMockupTab(data) {
  const host = $("#mockup-host");
  const rhost = $("#render-host");
  const bar = document.getElementById("render-toolbar");
  if (!host) return;
  const mode = state.mockupMode || "frontend";
  if (bar) bar.hidden = mode !== "frontend";

  if (mode === "backend") {
    if (rhost) rhost.hidden = true;
    host.hidden = false;
    host.innerHTML = data.mockup_backend_html || data.mockup_html
      || "<em>No backend skeleton.</em>";
    return;
  }

  // frontend: the ENGINE render or an honest loading state. The mockup is
  // NEVER shown as a stand-in: flashing an approximation and then swapping
  // to the real pages reads as two contradictory previews, and on a
  // machine where the engine cannot run the approximation lingered as if
  // it were the real output (work-machine verified: every report "did not
  // compare to any preview"). The mockup appears ONLY on engine failure,
  // wrapped in an unmissable notice saying exactly what it is.
  if ((state.renderedPages && state.renderedPages.length)
      || state.renderedPdf) {
    _showRenderedPages();
    return;
  }
  if (rhost) { rhost.hidden = true; rhost.innerHTML = ""; }
  host.hidden = false;
  if (state._renderFailed) {
    host.innerHTML =
      '<div class="mockup-fallback-note">⚠ This is the APPROXIMATE '
      + "browser mockup — NOT the real render. The report engine "
      + "could not run on this machine: "
      + escHtml(String(state._renderFailed).slice(0, 300))
      // The same next-step table every other failure uses. A render that
      // cannot run on this machine leaves the .rdl untouched, and saying so
      // is the difference between "the tool is broken" and "the preview is".
      + " <b>Do this next:</b> "
      + escHtml(_nextActionFor("render_engine_unavailable"))
      + ' <button class="btn-tiny" id="render-retry" type="button">'
      + "Retry engine render</button></div>"
      + (data.mockup_html || "<em>No mockup available.</em>");
    const rb = document.getElementById("render-retry");
    if (rb) rb.addEventListener("click", () => {
      state._renderFailed = null;
      renderMockupTab(state.data);
    });
    _renderStatus("Engine render unavailable — approximate mockup "
      + "shown with a notice.", "warn");
    return;
  }
  // Never a bare spinner: the spinner is aria-hidden decoration and the
  // sentence beside it IS the status. Under prefers-reduced-motion the CSS
  // stops the spin and the words carry on saying the same thing.
  host.innerHTML = RENDER_LOADING_HTML;
  _renderStatus(RENDER_WORKING_LINE, "working");
  if (!state._renderInFlight) runRenderPreview();
}

// The working state for a render, in one place because there are FOUR ways
// to start one (a fresh conversion, the Re-render button, the retry button
// in the fallback notice, and the frontend/backend toggle) and only the
// first of them used to show anything.
const RENDER_LOADING_HTML =
  '<div class="render-loading">'
  + '<div class="render-spinner" aria-hidden="true"></div>'
  + '<p class="render-loading-text">Rendering through Microsoft’s report '
  + 'engine… the preview will be exactly what the .rdl prints. This '
  + 'usually takes a few seconds and there is no percentage for it — '
  + 'the engine answers when the pages are ready.</p></div>';
const RENDER_WORKING_LINE =
  "Rendering the pages… no percentage for this step; the engine answers "
  + "when it is finished.";

// THE DEFECT THIS EXISTS FOR, measured on the real page: pressing
// "Re-render" called runRenderPreview() directly, and the ONLY thing that
// changed on screen was that the button went disabled. The render toolbar
// kept its previous line -- "✓ Done — Rendered by Microsoft's report engine
// — 4 page(s)" -- for the entire run, so a long operation ran under a
// message saying it had already finished. With no audio channel that is
// indistinguishable from a hang, and worse than silence because the screen
// actively says the opposite of what is happening.
//
// So the working state is set HERE, inside the one function that actually
// starts a render, and it is a WORD ("Working"), a glyph, a sentence, a
// button that renames itself, and aria-busy -- never a disabled button on
// its own.
function _renderBusy(on) {
  const btn = document.getElementById("render-run");
  if (btn) {
    if (on) {
      if (!btn.dataset.idleLabel) btn.dataset.idleLabel = btn.textContent;
      btn.textContent = "Rendering…";
      btn.setAttribute("aria-busy", "true");
    } else {
      btn.textContent = btn.dataset.idleLabel || btn.textContent;
      btn.removeAttribute("aria-busy");
    }
    btn.disabled = !!on;
  }
  if (!on) return;
  _renderStatus(RENDER_WORKING_LINE, "working");
  // Whatever pages are on screen belong to the render being replaced. Show
  // the loading block instead of leaving stale output up with no notice.
  if ((state.mockupMode || "frontend") !== "frontend") return;
  if (state.renderedPages || state.renderedPdf) return;   // nothing discarded
  const host = $("#mockup-host");
  const rhost = $("#render-host");
  if (rhost) { rhost.hidden = true; rhost.innerHTML = ""; }
  if (host) { host.hidden = false; host.innerHTML = RENDER_LOADING_HTML; }
}

// Is the toolbar still claiming a render is running? Read off the DOM, so
// the answer is what the user can actually see.
function _renderStatusState() {
  const n = document.getElementById("render-status");
  return n ? (n.getAttribute("data-state") || "") : "";
}

// THE DEFECT THIS REPLACES, measured on the real page: every rendered page
// was an <img alt="Rendered page 3">. That alt IDENTIFIES the picture and
// carries nothing that is IN it -- and these images are not decoration,
// they are the deliverable: the only place the operator sees what SSRS
// will print. WCAG 2.2 SC 1.1.1.
//
// So the alt says which report, which page of how many, what the page
// OPENS with and how much is on it -- the opening line and the counts come
// from the engine's own PDF (see _rendered_page_notes in backend/app.py),
// so they describe what the page really prints rather than what the report
// definition suggests it might. When that text cannot be trusted (a
// non-Latin subset extracts as glyph ids) the server sends no heading and
// the alt degrades to the honest half: report, page number, and where the
// text lives instead.
function _renderPageNote(i) {
  const notes = state.renderedNotes || [];
  return notes[i] || null;
}

function _renderedPageAlt(i, total) {
  const name = (state.data && state.data.report && state.data.report.name) || "";
  const note = _renderPageNote(i);
  let s = "Page " + (i + 1) + " of " + total + " of "
        + (name ? "the report " + name : "the report")
        + ", as Microsoft’s report engine prints it.";
  if (note && note.heading) s += " Begins “" + note.heading + "”.";
  if (note && note.lines) {
    s += " " + note.lines + " line" + (note.lines === 1 ? "" : "s")
       + " of text";
    s += note.words ? ", about " + note.words + " words." : ".";
  }
  s += " Picture of the printed page — the same report as text is in the "
     + "RDL XML view, and label by label in the Backend preview.";
  return s;
}

// The visible caption above each page. It carries the page's own opening
// line when there is one, so the stack of pages can be navigated by
// reading rather than by squinting at four thumbnails of grey text.
function _renderedPageLabel(i, total) {
  const note = _renderPageNote(i);
  return "Page " + (i + 1) + " of " + total
       + (note && note.heading ? " — " + note.heading : "");
}

// A picture of a page is not a way to READ a page. Both text routes the
// app already has are one keystroke from the render view instead of being
// something the operator has to know about: the Backend preview (every
// label and data field, as text) and the RDL XML view (the definition
// itself). This bar is the render view's text alternative.
function _renderTextRouteBar() {
  const bar = el("div", { class: "render-bar" },
    el("span", { class: "render-bar-text",
                 text: "These pages are pictures of the printed report. "
                     + "To read the same report as text:" }),
    el("span", { class: "render-bar-actions" },
      el("button", {
        class: "btn-tiny", id: "render-as-labels", type: "button",
        text: "Backend view — every label and field",
        onclick: () => {
          _setMockupMode("backend");
          // Measured: the switch hides #render-host, this button goes with
          // it, and a focused element that becomes hidden drops focus to
          // <body> -- the keyboard user is dumped at the top of the
          // document. Land on the control that now says where they are.
          const b = document.getElementById("mockup-mode-backend");
          if (b) b.focus();
        },
      }),
      el("button", {
        class: "btn-tiny", id: "render-as-rdl", type: "button",
        text: "RDL XML",
        onclick: () => {
          activateTab("rdl");
          const t = document.getElementById("tab-btn-rdl");
          if (t) t.focus();          // keyboard focus follows the jump
        },
      })));
  return bar;
}

function _showRenderedPages() {
  const host = $("#mockup-host");
  const rhost = $("#render-host");
  if (!rhost) return;
  if (host) host.hidden = true;
  rhost.hidden = false;
  rhost.innerHTML = "";
  rhost.appendChild(_renderTextRouteBar());
  if (state.renderedPdf) {
    const emb = document.createElement("embed");
    emb.src = state.renderedPdf;
    emb.type = "application/pdf";
    // Not title=: a tooltip needs a mouse. An embed with no name is "blank"
    // in the accessibility tree.
    emb.setAttribute("aria-label",
      "The rendered report as a PDF, shown in the browser’s own PDF viewer. "
      + "The same report as text is in the RDL XML view.");
    emb.style.width = "100%";
    emb.style.height = "72vh";
    rhost.appendChild(emb);
    _renderStatus("Rendered by Microsoft’s report engine at "
      + (state.renderedRows || 3) + " sample row(s) — shown as the "
      + "PDF itself. " + (state.renderedNote || ""), "done");
    return;
  }
  const pages = state.renderedPages || [];
  pages.forEach((src, i) => {
    const lab = document.createElement("div");
    lab.className = "render-page-label";
    lab.textContent = _renderedPageLabel(i, pages.length);
    const img = document.createElement("img");
    img.src = src;
    img.alt = _renderedPageAlt(i, pages.length);
    rhost.appendChild(lab);
    rhost.appendChild(img);
  });
  _renderStatus("Rendered by Microsoft’s report engine — "
    + pages.length + " page(s) at " + (state.renderedRows || 3)
    + " sample row(s). Values are placeholders; the report server fills "
    + "them from live data."
    + (state.renderedNote ? " " + state.renderedNote : ""), "done");
}

async function runRenderPreview() {
  if (!state.data) { toast("Convert a report first.", "warn"); return; }
  const rowsEl = document.getElementById("render-rows");
  const rows = Math.max(1, Math.min(25,
    parseInt(rowsEl && rowsEl.value, 10) || 3));
  const token = state._renderToken || 0;
  state._renderInFlight = true;
  _renderBusy(true);
  try {
    const fd = new FormData();
    fd.append("rows", String(rows));
    // With a report server configured, the server renders the preview --
    // zero local dependencies, real expression evaluation.
    const rsu = getReportServerUrl();
    if (rsu) fd.append("report_server_url", rsu);
    const r = await fetch("/api/render-preview", { method: "POST", body: fd });
    const j = await r.json();
    if ((state._renderToken || 0) !== token) return;  // a newer conversion won
    if (!r.ok || j.error) {
      state._renderFailed = j.error || "engine render failed";
      // A degraded preview, not a lost conversion: the .rdl is still fine.
      // It goes in the durable record so the reason is still readable later,
      // but it does not raise the assertive alert -- that is for work that
      // actually failed.
      statusRecord("warn", "The live preview could not run on this machine",
                   { subject: (state.data && state.data.report
                               && state.data.report.name) || "",
                     detail: String(state._renderFailed).slice(0, 200) });
      if (state.mockupMode !== "backend" && state.data)
        renderMockupTab(state.data);
      else
        _renderStatus("The report engine could not run on this machine: "
          + escHtml(String(state._renderFailed).slice(0, 200)), "warn");
      return;
    }
    if (j.pdf) {
      // No PyMuPDF on this machine: the server sent the rendered PDF
      // itself. Browsers display PDFs natively, so the preview still IS
      // the real engine output -- just not split into page images.
      state.renderedPages = null;
      state.renderedPdf = j.pdf;
      state.renderedNotes = null;
      state.renderedRows = rows;
      state.renderedNote = j.note || "";
      if ((state.mockupMode || "frontend") === "frontend") _showRenderedPages();
      else _renderStatus("The pages are rendered. Switch to Frontend to "
                         + "see them.", "done");
      return;
    }
    state.renderedPdf = null;
    state.renderedPages = j.pages;
    // What each page SHOWS, read off the engine's own PDF -> the alt text.
    state.renderedNotes = j.page_notes || null;
    state.renderedRows = rows;
    state.renderedNote = j.note || "";
    if ((state.mockupMode || "frontend") === "frontend") _showRenderedPages();
    else _renderStatus("The pages are rendered. Switch to Frontend to "
                       + "see them.", "done");
  } catch (e) {
    if ((state._renderToken || 0) === token) {
      state._renderFailed = String(e);
      if (state.mockupMode !== "backend" && state.data)
        renderMockupTab(state.data);
      else
        _renderStatus("The preview could not be rendered: "
          + escHtml(String(e).slice(0, 200)), "warn");
    }
  } finally {
    state._renderInFlight = false;
    _renderBusy(false);
    // A newer conversion superseded this render while it ran: its response
    // was discarded above and nothing repainted, leaving the status stuck
    // on "rendering...". Kick one fresh render for the CURRENT conversion.
    const superseded = (state._renderToken || 0) !== token;
    if (superseded && !state.renderedPages
        && !state._renderFailed && state.data
        && (state.mockupMode || "frontend") === "frontend") {
      runRenderPreview();
    } else if (_renderStatusState() === "working") {
      // Nothing wrote an outcome -- a superseded render with nothing to
      // re-run behind it. Saying "Working" forever is the defect this
      // whole block exists for, so say the true thing instead.
      _renderStatus("The preview stopped before it finished. Press "
        + "Re-render to try again.", "warn");
      _renderBusy(false);
    }
  }
}

// Wire toggle buttons once on load. We attach immediately if the DOM is
// already ready, otherwise wait for DOMContentLoaded — app.js may be
// included near the end of <body>, in which case the listener never fires.
function _wireMockupToggle() {
  MOCKUP_MODES.forEach((m, i) => {
    const b = document.getElementById("mockup-mode-" + m);
    if (b && !b._wired) {
      b.addEventListener("click", () => _setMockupMode(m));
      // role="radio" carries a promise: the group is ONE tab stop and the
      // arrow keys move inside it. _setMockupMode already rolls the
      // tabindex; this is the other half of the bargain.
      b.addEventListener("keydown", (e) => {
        const STEP = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
        let j = null;
        if (e.key in STEP) j = (i + STEP[e.key] + MOCKUP_MODES.length) % MOCKUP_MODES.length;
        else if (e.key === "Home") j = 0;
        else if (e.key === "End") j = MOCKUP_MODES.length - 1;
        if (j == null) return;
        e.preventDefault();
        _setMockupMode(MOCKUP_MODES[j]);
        const target = document.getElementById("mockup-mode-" + MOCKUP_MODES[j]);
        if (target) target.focus();
      });
      b._wired = true;
    }
  });
  const rr = document.getElementById("render-run");
  if (rr && !rr._wired) {
    rr.addEventListener("click", () => {
      if (state._renderInFlight) return;      // it is already running
      if (!state.data) { _needAReportFirst(); return; }
      // The pages on screen are about to be replaced, so they stop being
      // the truth here -- runRenderPreview raises the working state for
      // whatever is left.
      state.renderedPages = null;
      state.renderedPdf = null;
      state.renderedNotes = null;
      state._renderFailed = null;
      runRenderPreview();
    });
    rr._wired = true;
  }
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _wireMockupToggle);
} else {
  _wireMockupToggle();
}

// ----- Code pane text (display-capped) -----
// Even with highlighting skipped, un-hiding a panel makes the browser
// LINE-BREAK the whole code block from scratch — a multi-megabyte RDL in
// one <pre> is ~1.6 s of frozen layout per tab switch (measured), twice
// that for the side-by-side pair. Nobody reads 3 MB of XML in a pane; cap
// what's DISPLAYED and say so honestly. Downloads always carry the full
// file — this touches only the on-screen copy.
// 120 KB ≈ 2,500 XML lines on screen — far more than anyone scrolls, and
// small enough that the highlighted span tree re-lays-out in tens of ms.
// (At 300 KB the highlighted block still cost ~0.6–1 s per tab switch.)
const CODE_DISPLAY_MAX = 120000;
function setCodeText(codeEl, fullText) {
  if (!codeEl) return;
  const txt = fullText || "";
  if (txt.length <= CODE_DISPLAY_MAX) {
    codeEl.textContent = txt;
    return;
  }
  codeEl.textContent =
    "<!-- Oracle2SSRS: large document — showing the first " +
    CODE_DISPLAY_MAX.toLocaleString() + " of " + txt.length.toLocaleString() +
    " characters. The download/deploy files are always complete. -->\n" +
    txt.slice(0, CODE_DISPLAY_MAX) +
    "\n<!-- … truncated for display — use Download for the full file … -->";
}

// ----- Tab 2: RDL XML -----
function renderRdlTab(data) {
  setCodeText($("#rdl-code"), data.rdl_xml || "");
}

// ----- Tab 3: Side-by-side -----
function renderSideBySideTab(data) {
  setCodeText($("#oracle-code"), data.oracle_xml || "");
  setCodeText($("#rdl-code-2"), data.rdl_xml || "");
}

// ----- Tab 4: Live data -----
function renderLiveTab(data) {
  const host = $("#live-host");
  if (!host) return;
  host.innerHTML = "";
  const r = data.report || {};
  const queries = r.queries || [];
  const params  = r.parameters || [];
  const intro = el("p", { class: "panel-intro" });
  intro.innerHTML =
    "This runs the report's SQL against a small read-only <b>sample " +
    "database</b> that ships with this tool, to prove the SQL is valid and " +
    "runs. Tables the sample database does not have come back empty. " +
    "It does <b>not</b> touch your database. The connection you type in the " +
    "panel on the left is baked into the downloaded RDL for your report " +
    "server to use later; it is not used here.";
  host.appendChild(intro);
  if (!queries.length) {
    host.appendChild(el("div", { class: "results-empty",
      text: "This report has no queries, so there is nothing to run here." }));
    return;
  }
  queries.forEach((q, idx) => {
    const card = el("div", { class: "query-card" });
    const qname = q.name || ("Query " + (idx + 1));
    const head = el("div", { class: "query-head" },
      el("h3", { class: "query-name", text: qname }),
      // "Run query" repeated once per card is ambiguous out of context;
      // the query name rides along for anyone reading the buttons alone.
      el("button", { class: "btn btn-primary", type: "button",
                     "aria-label": "Run query " + qname,
                     onClick: () => runQuery(card, q) }, "Run query")
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
    host.appendChild(el("div", { class: "results-empty",
      text: "The query ran and came back with no rows." }));
    return;
  }
  const wrap = el("div", { class: "results-table-wrap" });
  const tbl  = el("table", { class: "results-table" });
  // scope="col" is what pairs a value cell with its column name when the
  // table is read one cell at a time; a caption names the table itself.
  tbl.appendChild(el("caption", { class: "sr-only",
    text: "Query results: " + rows.length + " row(s), " + cols.length + " column(s)" }));
  const thead = el("thead", {}, el("tr", {},
    ...cols.map(c => el("th", { scope: "col", text: c }))));
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
  // One verdict, decided in one place (conversionVerdict), so this strip
  // cannot say "Ready" while the banner beside it says "Check this".
  const v = conversionVerdict(data);
  const blockers = v.blockers, reds = v.runtime;
  const name = (data.report && data.report.name) || "Report";
  // The glyph used to be "!" for BOTH a warning and a blocker, so colour was
  // the only difference between "it will run with problems" and "it will not
  // load at all". Distinct glyph, distinct word, distinct edge shape (CSS
  // reads data-verdict), and only then colour.
  let cls, verdict, glyph, label, vkey;
  if (blockers) {
    cls = "ds-blocker"; vkey = "blocker";
    glyph = statusState("failed").glyph; label = "Blocked";
    verdict = "Fix " + blockers + " problem" + (blockers > 1 ? "s" : "")
            + " before you upload this";
  } else if (reds) {
    cls = "ds-warn"; vkey = "runtime";
    glyph = statusState("warn").glyph; label = "Check this";
    verdict = reds + " thing" + (reds > 1 ? "s" : "")
            + " may go wrong when it runs — see Validation";
  } else {
    cls = "ds-ready"; vkey = "ready";
    glyph = statusState("done").glyph; label = "Ready";
    verdict = "Ready to upload";
  }
  host.className = "deploy-status " + cls;
  host.setAttribute("data-verdict", vkey);
  host.innerHTML =
    '<div class="ds-left">' +
      '<span class="ds-glyph" aria-hidden="true">' + glyph + '</span>' +
      '<span class="ds-state">' + label + '</span>' +
      '<span class="ds-name">' + escapeHtml(name) + '</span>' +
      '<span class="ds-verdict">' + verdict + '</span>' +
    '</div>' +
    '<div class="ds-steps"><b>1</b> Download the .rdl <span>→</span> <b>2</b> Upload it to SSRS ' +
      (getSharedDsPath()
        ? '<span>→</span> <b>3</b> Run it (it already points at ' + escapeHtml(getSharedDsPath()) + ')</div>'
        : '<span>→</span> <b>3</b> Point it at your data source <span>→</span> <b>4</b> Run it &nbsp;<em>(do not click Refresh Fields — the field list is already complete)</em></div>') +
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
    note.innerHTML = "⤷ <b>Rows in this report open:</b> " +
      uniq.map(escapeHtml).join(", ") +
      " — build " + (uniq.length > 1 ? "these reports" : "this report") +
      " in the <b>Sub-Reports</b> view, then upload " + (uniq.length > 1 ? "them" : "it") +
      " to SSRS <b>before</b> this one. SSRS matches the link by name at the " +
      "moment someone clicks it, so the linked report has to be there already. " +
      "The Sub-Reports view has the full steps.";
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
  // INFO = informational disclosures (e.g. fidelity.needs_attention when the
  // fidelity headline is below full coverage). They never change the verdict,
  // but they ARE findings: rendered in the list and counted in the badge so
  // the preflight surface and the fidelity card tell one story.
  const infos = count("INFO");

  // The verdict itself comes from the one place that decides it, so this
  // banner cannot call a file ready while the checklist beside it is
  // quoting errors out of that same file. (The counts above stay local:
  // they are what the Validation BADGE totals, not a second verdict.)
  const vd = conversionVerdict(data);

  // Deep expression verification: every generated VB.NET expression was compiled
  // through the real System.CodeDom compiler (the same compilation SSRS performs
  // at publish). A clean pass is strong evidence the report won't throw #Error.
  const ev = pf.expr_verify;
  const evReady = !!(ev && ev.available && ev.summary &&
                     ev.summary.failed === 0 && ev.summary.total > 0);

  const banner = document.getElementById("preflight-banner");
  if (banner) {
    // Four verdicts, four SHAPES and four WORDS -- not four tints. The
    // classes below (pf-*) had no stylesheet rule at all when this was
    // written, which meant a BLOCKER banner and a READY banner painted
    // identically; data-verdict is what the stylesheet now reads, and the
    // glyph + the state word are readable with no stylesheet whatsoever.
    let cls, label, sub, vkey, glyph;
    if (pf.source_kind) {
      // Partial Oracle artifact (customization overlay / data-model-only /
      // layout fragment) — be honest: it's not a full report.
      cls = "pf-red"; vkey = "partial";
      glyph = statusState("warn").glyph;
      // These three words are Oracle's, not the reader's. Say what the
      // file IS, then let the backend's own message carry the detail.
      const nice = { customization_overlay: "a set of changes to another report",
                     data_model_only: "the data half of a report, with no layout",
                     layout_fragment: "part of a layout, not a whole report" }[pf.source_kind]
                   || "only part of a report";
      label = glyph + " Check this — this file is " + nice;
      sub = pf.source_kind_message
            || "Export the whole report from Oracle Reports and drop that file instead.";
      banner.className = "preflight-banner " + cls;
      banner.setAttribute("data-verdict", vkey);
      banner.innerHTML = "<b>" + label + "</b><span>" + sub + "</span>";
      banner.hidden = false;
      return;
    }
    if (vd.key === "blocker") {
      cls = "pf-blocker"; vkey = "blocker";
      glyph = statusState("failed").glyph;
      label = glyph + " Blocked — " + vd.blockers + " problem"
            + (vd.blockers > 1 ? "s" : "") + " to fix";
      sub = "This will not load and refresh cleanly on your report server. "
          + "Open Validation, fix what it lists, then convert again.";
    } else if (vd.key === "runtime") {
      cls = "pf-red"; vkey = "runtime";
      glyph = statusState("warn").glyph;
      label = glyph + " Check this — " + vd.runtime + " thing"
            + (vd.runtime > 1 ? "s" : "") + " may go wrong when it runs";
      sub = "It will upload, but something is likely to come out wrong once "
          + "the report runs. Open Validation to see what and why"
          + (vd.sqlErrors
             ? " — the Deploy Checklist quotes " + vd.sqlErrors
               + " of them beside the query they came from."
             : ".");
    } else {
      cls = "pf-ready"; vkey = "ready";
      glyph = statusState("done").glyph;
      label = glyph + " Ready to upload";
      const notes = ambers + infos;
      sub = notes
        ? (notes + " note" + (notes > 1 ? "s" : "") + " to read in Validation. "
           + "None of them stop you.")
        : "Nothing in the file is standing in your way.";
      if (evReady) {
        sub = "All " + ev.summary.total + " calculation"
            + (ev.summary.total > 1 ? "s" : "") + " in this report compiled "
            + "successfully. " + sub;
      }
      // ...and it may not say that while the Deploy Checklist still has
      // steps a person has to do. The steps are the checklist's own, by
      // its own status, so the two surfaces cannot describe different work.
      if (vd.needsHuman.length) {
        sub += " " + vd.needsHuman.length + " step"
             + (vd.needsHuman.length > 1 ? "s" : "")
             + " in the Deploy Checklist still need"
             + (vd.needsHuman.length === 1 ? "s" : "") + " you: "
             + vd.needsHuman.join("; ") + ".";
      }
    }
    banner.className = "preflight-banner " + cls;
    banner.setAttribute("data-verdict", vkey);
    banner.innerHTML = "<b>" + label + "</b><span>" + sub + "</span>";
    banner.hidden = false;
  }

  // Validation-tab detail: prepend an ordered preflight issue list.
  const vhost = document.getElementById("validation-host");
  if (vhost && (issues.length || evReady)) {
    const sec = document.createElement("div");
    sec.className = "preflight-detail";
    let html = "<h3>Checks made before you upload</h3>";
    if (evReady) {
      html += "<div class='pf-issue pf-ok'>" +
              "<span class='pf-sev'>✓</span>" +
              "<code class='pf-rule'>rdl.expr_compile</code>" +
              "<span class='pf-msg'>All " + ev.summary.total +
              " calculation(s) this tool wrote were compiled with the real " +
              "compiler — the same one SSRS uses when you publish. None of " +
              "them will print as an error on the page.</span></div>";
    }
    // Severity is a WORD and a GLYPH here, never a tint: this list is read
    // by someone deciding what to fix first, and on a greyscale screen or a
    // printout the tint is gone.
    const SEV_GLYPH = { BLOCKER: statusState("failed").glyph,
                        RED: statusState("warn").glyph,
                        AMBER: statusState("warn").glyph,
                        INFO: "ℹ" };
    ["BLOCKER", "RED", "AMBER", "INFO"].forEach(sev => {
      issues.filter(i => (i.severity || "").toUpperCase() === sev).forEach(i => {
        html += "<div class='pf-issue pf-" + sev.toLowerCase() + "'"
                + " data-severity='" + sev.toLowerCase() + "'>" +
                "<span class='pf-sev'><span aria-hidden='true'>"
                + SEV_GLYPH[sev] + "</span> " + sev + "</span>" +
                "<code class='pf-rule'>" + escapeHtml(i.rule || "") + "</code>" +
                "<span class='pf-msg'>" + escapeHtml(i.message || "") + "</span></div>";
      });
    });
    sec.innerHTML = html;
    vhost.insertBefore(sec, vhost.firstChild);
  }

  // Validation tab chip = the upload-blocking issues (most important signal)
  // PLUS informational preflight findings (fidelity below full coverage must
  // be counted, or the badge says "0 findings" while the fidelity card says
  // e.g. 67% — the two surfaces would disagree about needing attention).
  const v = data.validation_issues || [];
  const vSerious = v.filter(i => i.severity === "error" || i.severity === "warning").length;
  setBadge("badge-validate", blockers + reds + vSerious + infos);
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
      const chipGlyph = { error: statusState("failed").glyph,
                          warning: statusState("warn").glyph,
                          info: "ℹ" }[k] || "ℹ";
      summary.appendChild(el("span",
        { class: "sev-chip sev-" + k, "data-severity": k },
        el("span", { "aria-hidden": "true", text: chipGlyph }),
        " " + counts[k] + " " + k));
    });
  }
  if (!issues.length) {
    host.appendChild(el("div", { class: "results-empty",
      text: "No problems found in this report's SQL." }));
    return;
  }
  ["error", "warning", "info"].forEach(sev => {
    const matching = issues.filter(i => (i.severity || "info") === sev);
    if (!matching.length) return;
    // h3: this panel's own title is the h2, so a severity group is one
    // level down. h4 here skipped a level and broke heading navigation.
    const headGlyph = { error: statusState("failed").glyph,
                        warning: statusState("warn").glyph,
                        info: "ℹ" }[sev] || "ℹ";
    host.appendChild(el("h3", { class: "sev-head sev-" + sev,
                                "data-severity": sev },
      el("span", { "aria-hidden": "true", text: headGlyph }),
      " " + sev.toUpperCase() + " (" + matching.length + ")"));
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
    host.appendChild(el("div", { class: "results-empty",
      text: "No steps to show yet. Convert a report first — the steps are "
          + "built from what that report needs." }));
    return;
  }
  checklist.forEach(step => {
    const status = step.status || "todo";
    const icon = ({ auto: "✔", todo: "🛠", manual: "○", caution: "⚠" })[status] || "○";
    // "auto" / "todo" / "manual" / "caution" are the backend's words for
    // itself. On screen they have to say who does the work.
    const chip = ({ auto: "Done for you", todo: "Needs finishing",
                    manual: "You do this", caution: "Take care" })[status]
                 || "You do this";
    const card = el("details", { class: "deploy-step deploy-" + status, open: step.step <= 3 });
    card.appendChild(el("summary", {},
      el("span", { class: "deploy-icon", text: icon }),
      el("span", { class: "deploy-num", text: " " + step.step + ". " }),
      el("span", { class: "deploy-title", text: step.title || "" }),
      el("span", { class: "deploy-status-chip", "data-status": status,
                   text: chip })
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
function renderFidelityCard(host, fid, preflight) {
  const pf = preflight || {};
  const verdict = pf.verdict;
  if (pf.source_kind) {
    // Partial Oracle artifact: fidelity scores a COMPLETE report's
    // source->RDL coverage. Scoring a customization overlay or a
    // data-model-only export "100%" would be a lie of scope -- render an
    // honest not-applicable card instead of a percentage.
    const section = document.createElement("section");
    section.className =
      "extras-section extras-compact fidelity-card fidelity-partial";
    section.innerHTML =
      "<h3>Conversion fidelity <span class='fidelity-score'>&mdash;</span></h3>" +
      "<p class='fidelity-blurb'><b>Not applicable.</b> This figure " +
      "describes a whole report, and this file is only part of one. " +
      escapeHtml(pf.source_kind_message || pf.source_kind) + "</p>";
    host.appendChild(section);
    return;
  }
  if (!fid || typeof fid.score !== "number") return;
  const cats = fid.categories || {};
  const lf = cats.layout_fields || {};
  // HONESTY: the binding score alone must never be presented as "1:1".
  // It measures whether source columns/parameters reached the RDL — NOT
  // whether the report LOOKS like the Oracle original. A report can bind
  // every column and still lay out wrong. So the headline is the WORST of
  // the measured axes, and the claim is downgraded whenever the deployment
  // verdict is unhappy or part of the layout never displays.
  const disp = (typeof lf.display_coverage === "number") ? lf.display_coverage : 1;
  const blocked = verdict === "BLOCKER" || verdict === "RED";
  const headline = Math.min(fid.score, disp);
  const pct = Math.round(headline * 100);
  const full = headline >= 1 && !blocked;
  const section = document.createElement("section");
  section.className = "extras-section extras-compact fidelity-card " +
    (full ? "fidelity-full" : "fidelity-partial");

  const C = cats;
  const stat = [];
  if (C.columns)   stat.push(["Columns",    C.columns.preserved   + " / " + C.columns.total]);
  if (C.parameters)stat.push(["Parameters", C.parameters.preserved + " / " + C.parameters.total]);
  if (C.layout_fields) stat.push(["Layout fields", C.layout_fields.bound + " / " + C.layout_fields.total]);
  if (C.formulas && C.formulas.total) stat.push(["Formulas to finish by hand", String(C.formulas.total)]);
  if (C.summaries) stat.push(["Totals and subtotals", String(C.summaries.aggregates_in_rdl || 0)]);

  if (typeof lf.display_coverage === "number") {
    stat.push(["Of the layout, printed", Math.round(disp * 100) + "%"]);
  }

  let blurb;
  if (blocked) {
    blurb = "<b>Do not deploy this yet.</b> The verdict is " +
      escapeHtml(verdict) + ". Fix what Validation lists before you trust " +
      "this output, whatever this figure says.";
  } else if (disp < 1) {
    blurb = "<b>Everything is wired up, but the page is missing " +
      "things.</b> " + Math.round((1 - disp) * 100) + "% of the columns " +
      "Oracle put on the page are not printed by the new file. The report " +
      "will run, but it will not look like the original.";
  } else if (full) {
    blurb = "Every column and parameter came across, and every column " +
      "Oracle put on the page is printed.";
  } else {
    blurb = "Some columns or parameters did not make it into the new " +
      "file. The list below names them.";
  }

  let html =
    "<h3>Conversion fidelity " +
      "<span class='fidelity-score'>" + pct + "%</span></h3>" +
    "<div class='fidelity-bar'><span style='width:" + pct + "%'></span></div>" +
    "<p class='fidelity-blurb'>" + blurb +
      " <span class='fidelity-note'>This figure counts things, not " +
      "looks: columns and parameters carried across, and layout columns the " +
      "new file actually prints. It cannot tell you whether the page looks " +
      "right, because this tool never sees a picture of your original. " +
      "Open <b>Preview</b> and compare it with a known-good run before you " +
      "deploy.</span>" +
    "</p>";

  html += "<div class='fidelity-stats'>";
  stat.forEach(([k, v]) => {
    html += "<div class='fidelity-stat'><span>" + escapeHtml(k) + "</span><b>" + escapeHtml(v) + "</b></div>";
  });
  html += "</div>";

  const needs = fid.needs_attention || [];
  if (needs.length) {
    html += "<details class='fidelity-needs'" + (full ? "" : " open") + ">" +
      "<summary>" + needs.length + " thing(s) to check or finish</summary><ul>";
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
  renderFidelityCard(host, data.fidelity_report, data.preflight || {});

  // Compact card: Bursting / DDS summary
  const burst = (data && data.bursting) || {};
  const burstSection = document.createElement("section");
  burstSection.className = "extras-section extras-compact";
  // "DDS" is Microsoft's abbreviation for a data-driven subscription. It
  // meant nothing to a reader who has not met the term, so the heading says
  // what the report actually does.
  const burstTitle = burst.is_bursting
    ? "Bursting — this report sends a copy per recipient"
    : "Bursting — not used by this report";
  burstSection.innerHTML = "<h3>" + burstTitle + "</h3>";
  if (burst.is_bursting) {
    burstSection.innerHTML +=
      "<div class='extras-meta'>" +
      "<b>One copy per:</b> " + escapeHtml(burst.burst_key_field || "?") +
      " &middot; <b>File named:</b> " + escapeHtml(burst.filename_pattern || "?") +
      "</div>";
    if (burst.burst_query) {
      burstSection.innerHTML +=
        "<details><summary>The SQL that lists the recipients <button class='btn btn-ghost btn-copy' data-copy='burst_query'>Copy</button></summary>" +
        "<pre class='code-block'><code class='language-sql'>" + escapeHtml(burst.burst_query) + "</code></pre></details>";
    }
    if (burst.powershell_script) {
      burstSection.innerHTML +=
        "<details><summary>The PowerShell script that does the sending <button class='btn btn-ghost btn-copy' data-copy='powershell_script'>Copy</button></summary>" +
        "<pre class='code-block'><code>" + escapeHtml(burst.powershell_script) + "</code></pre></details>";
    }
  }
  host.appendChild(burstSection);

  // Compact card: AI-assist prompts
  const prompts = data.ai_prompts || [];
  const promptSection = document.createElement("section");
  promptSection.className = "extras-section extras-compact";
  promptSection.innerHTML =
    "<h3>Questions you can paste into an AI tool (" + prompts.length + ")</h3>" +
    "<p class='panel-intro'>Ready-made questions about this report's queries " +
    "and formulas. Copy one into whichever AI tool you already use if you " +
    "want a second opinion. Nothing is sent anywhere by this tool.</p>";
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
  auditSection.innerHTML =
    "<h3>What this tool changed, step by step (" + trail.length + ")</h3>" +
    "<p class='panel-intro'>Every decision made while converting, in order, " +
    "with the text before and after. Use it when you need to explain why " +
    "something came out the way it did.</p>";
  if (trail.length) {
    let table = "<div class='extras-table-wrap'><table class='extras-table'>" +
      "<caption class='sr-only'>Audit trail: every conversion decision, in order</caption>" +
      "<thead><tr>" +
      "<th scope='col'>#</th><th scope='col'>Stage</th><th scope='col'>Scope</th>" +
      "<th scope='col'>Rule</th><th scope='col'>Before</th><th scope='col'>After</th>" +
      "</tr></thead><tbody>";
    trail.forEach(e => {
      table += "<tr>" +
        "<th scope='row'>" + escapeHtml(String(e.step || "")) + "</th>" +
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
        () => clipboardRefused()
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
      const on = document.body.classList.toggle("show-advanced");
      adv.textContent = on ? "Simple view" : "Advanced views";
      // A disclosure has to SAY whether it is open, and revealing/hiding
      // tabs changes which of them can hold the strip's single tab stop.
      adv.setAttribute("aria-expanded", on ? "true" : "false");
      syncTabRoving();
    });
  }
  const cta = document.getElementById("cta-download-rdl");
  if (cta) {
    cta.addEventListener("click", () => {
      if (!state.data) { _needAReportFirst(); return; }
      window.location.href = "/api/download/rdl";
    });
  }
}

// Show or hide the advanced tab group programmatically, keeping the toggle
// button's label in sync. Used when the UI must point the user at an
// advanced tab (fix-first CTA -> Validation, a demoted Bursting/Sub-Reports
// tab, the guided tour).
function setAdvancedTabs(on) {
  document.body.classList.toggle("show-advanced", !!on);
  const adv = document.getElementById("advanced-toggle");
  if (adv) {
    adv.textContent = on ? "Simple view" : "Advanced views";
    adv.setAttribute("aria-expanded", on ? "true" : "false");
  }
  syncTabRoving();
}
window.o2sSetAdvanced = setAdvancedTabs;

// The CTA bar under the preview is a DOWNLOAD prompt only when the artifact
// is actually worth downloading: preflight verdict READY/AMBER and a full
// report source. A BLOCKER/RED verdict -- or a partial-artifact source
// (customization overlay / data-model-only / layout fragment, i.e.
// preflight.source_kind is set) -- gets a fix-first bar pointing at the
// Validation tab instead of inviting the user to ship a broken file.
function showMockupCTA(data) {
  const cta = document.getElementById("mockup-cta");
  if (!cta) return;
  const pf = (data && data.preflight) || {};
  const partial = !!pf.source_kind;
  // Same verdict as the banner and the deploy strip: an invitation to
  // download is a claim about deployability, and this used to make it from
  // the preflight list alone while the checklist said the queries fail.
  const v = conversionVerdict(data);
  const deployable = !partial && v.key === "ready";
  cta.classList.toggle("cta-fix-first", !deployable);
  if (deployable) {
    cta.innerHTML =
      '<div class="cta-text"><b>Looks right?</b> ' +
      'Download the <code>.rdl</code> file, then upload it to your report ' +
      'server. The <b>Deploy Checklist</b> view has the full steps.</div>' +
      '<button id="cta-download-rdl" class="cta-btn">Download .rdl</button>';
    const btn = document.getElementById("cta-download-rdl");
    if (btn) btn.addEventListener("click", () => {
      if (!state.data) { _needAReportFirst(); return; }
      window.location.href = "/api/download/rdl";
    });
  } else {
    // The reason is the verdict's own counts, not preflight's letter grade:
    // that grade said READY on a report whose checklist was quoting four
    // errors, so this bar would have said "Fix this first. The verdict on
    // this report is READY."
    const why = partial
      ? "This file is only part of an Oracle report, not a whole one."
      : (v.key === "blocker"
         ? v.blockers + " problem" + (v.blockers > 1 ? "s" : "")
           + " stop" + (v.blockers === 1 ? "s" : "")
           + " it loading cleanly on your report server."
         : v.runtime + " thing" + (v.runtime > 1 ? "s" : "")
           + " may go wrong when it runs.");
    cta.innerHTML =
      '<div class="cta-text"><b>Fix this first.</b> ' + why +
      ' Open <b>Validation</b> to see exactly what was found and what to do ' +
      'about each thing.</div>' +
      '<button id="cta-open-validation" class="cta-btn">Open Validation</button>';
    const btn = document.getElementById("cta-open-validation");
    if (btn) btn.addEventListener("click", () => {
      setAdvancedTabs(true);
      activateTab("validate");
    });
  }
  cta.hidden = false;
}



// ----- Tab: Bursting / Email distribution -----
function renderBurstingTab(data) {
  const host = document.getElementById("burst-host");
  if (!host) return;
  host.innerHTML = "";

  const burst = (data && data.bursting) || {};

  // The Bursting tab earns MAIN-tab placement only when THIS report
  // actually distributed per recipient (bursting.is_bursting). Otherwise it
  // is demoted to the Advanced group so a dead "no bursting detected" panel
  // never sits front-and-centre for a report the feature can't apply to.
  const burstBtn = document.querySelector('.tab[data-tab="burst"]');
  if (burstBtn) {
    burstBtn.classList.toggle("tab-main", !!burst.is_bursting);
    burstBtn.classList.toggle("tab-adv", !burst.is_bursting);
  }
  // If the user was ON the bursting tab and this conversion demoted it out
  // of sight, fall back to the preview instead of stranding them on a
  // panel whose tab button vanished.
  if (!burst.is_bursting && state.activeTab === "burst" &&
      !document.body.classList.contains("show-advanced")) {
    state.activeTab = "mockup";
  }

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
      '<div class="burst-h-title">This report sends a copy to each recipient</div>' +
      '<div class="burst-h-meta">In Oracle it sent a separate PDF to every ' +
      '<code>' + escHtml(burst.burst_key_field || "recipient") + '</code>. ' +
      'The Standard edition of SSRS cannot do that on its own, so the ' +
      '<b>Burst Pack</b> below does it for you.</div></div>';
  } else {
    header.innerHTML =
      '<div class="burst-h-icon">○</div><div>' +
      '<div class="burst-h-title">This report does not send a copy per recipient</div>' +
      '<div class="burst-h-meta">It runs once and prints one set of pages, ' +
      'so there is nothing to split up here.</div></div>';
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
      '<li>In <b>Distribution Settings</b> above, set your email server and the ' +
        'address the mail is sent from. Then edit <b>the SQL that lists the ' +
        'recipients</b> so it returns <b>one row per person</b>: their email ' +
        'address and their <code>' + keyc + '</code>. A working example is ' +
        'filled in for you — point it at your own database.</li>' +
      '<li>Click <b>Download Burst Pack</b>. You get one <code>.zip</code> file. ' +
        '<b>Unzip it on your SSRS server.</b></li>' +
      '<li>Follow <code>service-account-setup.md</code> in the pack: it sets up a ' +
        'Windows account for the job and gives it permission to send mail. ' +
        'You do this <b>once</b>, and it then covers every report.</li>' +
      '<li>Run <code>Send-Reports.ps1</code> (right-click it, choose Run with ' +
        'PowerShell) to send yourself a test. When that works, schedule it in ' +
        'Task Scheduler. From then on, each person gets their own PDF.</li>' +
    '</ol>' +
    '<div class="burst-callout"><b>There is one thing only you can supply:</b> ' +
      'the recipient list — the SQL above that says who gets a copy and at ' +
      'which address. Sending the mail, making the PDFs, going through the ' +
      'recipients one by one, retrying, and not sending twice are all handled ' +
      'for you. The full walkthrough is in <code>README.md</code> inside the ' +
      'pack.</div>';
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
  addBlock("email_burst_query", "The SQL that lists the recipients",
    "This is the one query to replace with your own list of people and email addresses.", "sql");
  addBlock("email_powershell_script", "Send-Reports.ps1",
    "The script that does the work. It runs as the Windows account you set up, and reads its settings from burst.config.json each time it runs.", "");
  addBlock("email_config_template", "burst.config.json",
    "The settings, already filled in from Distribution Settings above.", "");
  const checklist = burst.service_account_checklist || [];
  if (checklist.length) {
    const wrap = document.createElement("div");
    wrap.className = "burst-block";
    wrap.innerHTML = '<div class="burst-files-sub">service-account-setup.md — the one-time setup on the server</div>';
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
        () => clipboardRefused()
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

// Turn a raw report name into readable link text: "ENVELOPE_REPORT_12" ->
// "Envelope Report 12". Short all-caps tokens (US, ID) stay as acronyms; digits stay.
// The exact wording (e.g. "Standard 12 x 9") isn't in the source, so this is a
// sensible STARTING point the user edits -- never blank, never the ugly raw name.
function _humanizeReportName(name) {
  return String(name || "").split(/[_\s]+/).filter(Boolean).map(t => {
    if (/^\d+$/.test(t)) return t;
    if (/^[A-Z0-9]{1,3}$/.test(t)) return t;       // acronym (US, ID)
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
  const nDetected = state.subreportChildren.filter(c => c.detected).length;
  // The Sub-Reports tab earns MAIN-tab placement only when this report
  // actually declares drill-through links (subreport_links). With none
  // detected it is demoted to the Advanced group, where the manual
  // "+ Add a sub-report" flow remains available for power users.
  if (tabBtn) {
    tabBtn.hidden = !state.data;
    tabBtn.classList.toggle("tab-main", nDetected > 0);
    tabBtn.classList.toggle("tab-adv", nDetected === 0);
  }
  // Never strand the user on a panel whose tab button just vanished.
  if (nDetected === 0 && state.activeTab === "subreports" &&
      !document.body.classList.contains("show-advanced")) {
    state.activeTab = "mockup";
  }
  setBadge("badge-subreports", nDetected);
  // The cover-hyperlink-text card only exists for reports that HAVE a
  // cover hyperlink -- on everything else it is noise about someone
  // else's report shape.
  const coverCard = document.getElementById("cover-link-section");
  if (coverCard) coverCard.hidden = nDetected === 0;

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

  const children = state.subreportChildren || [];
  const detected = children.filter(c => c.detected).length;
  // The sidebar dropzone section exists to feed DETECTED drill-through
  // links their child artifacts. A report that declares none hides it
  // entirely -- the manual "+ Add a sub-report" flow lives in the
  // Sub-Reports tab, where every child card carries its own uploader.
  if (!state.data || detected === 0) {
    section.hidden = true;
    slots.innerHTML = "";
    return;
  }
  section.hidden = false;
  if (hint) {
    hint.textContent =
      "Detected " + detected + " drill-through link" +
      (detected === 1 ? "" : "s") +
      ". Drop its files here and this tool will build it.";
  }

  slots.innerHTML = "";
  children.forEach(c => slots.appendChild(_subBuildSidebarSlot(c)));
}

function _subBuildSidebarSlot(c) {
  const wrap = el("div", { class: "subreport-slot", "data-child": c.name });
  // A real <button>, not a div with role="button" and a hand-rolled
  // keydown: Enter/Space, the announced role and the focus ring then come
  // from the platform and cannot drift out of sync.
  const dz = el("button", {
      class: "subreport-mini-drop", type: "button",
      "aria-label": "Drop or choose files for " + c.name,
    },
    el("span", { class: "subreport-mini-name", text: c.name }),
    el("span", { class: "subreport-mini-sub", text: "drop XML · .rdl · SQL, or click" }),
    (c.artifacts && c.artifacts.length)
      ? el("span", { class: "subreport-mini-have", text: c.artifacts.length + " file(s)" })
      : null
  );
  const input = el("input", { type: "file", multiple: true,
                              class: "file-input-hidden",
                              "aria-hidden": "true", tabindex: -1 });
  const msg = el("div", { class: "subreport-mini-msg" });
  const fire = (files) => { if (files && files.length) subUploadArtifacts(c.name, files, msg); };

  dz.addEventListener("click", () => input.click());
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
    : "the sub-report";
  const paramNote = params.length
    ? '<div class="burst-callout"><b>The main report hands these values over:</b> ' +
        params.map(p => '<code>' + escHtml(p) + '</code>').join(" ") +
        '. This tool has already marked them hidden in the sub-report, so ' +
        'opening the sub-report on its own never asks anyone for them.</div>'
    : '';
  const parentName = (state.data && state.data.report && state.data.report.name) || "your main report";
  const childName = names[0] || "the sub-report";
  const paramChips = params.length
    ? params.map(p => '<code>' + escHtml(p) + '</code>').join(" ")
    : "";
  return (
    '<section class="burst-section burst-guide o2s-howto">' +
      '<h3>How links to a second report work</h3>' +
      '<div class="burst-meta">Your <b>main report</b> (<code>' + escHtml(parentName) + '</code>) lists many ' +
        'records. A link on each row opens a <b>second report</b> (' + childList + ') about ' +
        '<i>that</i> record alone. A separate <b>“generate all”</b> link prints the whole set ' +
        '<b>in the same order</b> as the rows you just ran.</div>' +
      // ---- visual flow diagram ----
      '<div class="o2s-flow">' +
        '<div class="o2s-node o2s-main"><div class="o2s-node-title">' + escHtml(parentName) + '</div>' +
          '<div class="o2s-node-sub">the main report · every record</div></div>' +
        '<div class="o2s-links">' +
          '<div class="o2s-link"><span class="o2s-num">1</span><div>Click <b>one row’s</b> link ' +
            '<span class="o2s-arrow">&rarr;</span> the second report for <b>that row alone</b></div></div>' +
          '<div class="o2s-link"><span class="o2s-num">2</span><div>Click <b>“generate all”</b> ' +
            '<span class="o2s-arrow">&rarr;</span> <b>every one of them</b>, <em>in row order</em></div></div>' +
        '</div>' +
        '<div class="o2s-node o2s-child"><div class="o2s-node-title">' + escHtml(childName) + '</div>' +
          '<div class="o2s-node-sub">the sub-report(s)</div></div>' +
      '</div>' +
      (paramChips
        ? '<div class="burst-callout"><b>What gets handed over:</b> the main report gives the sub-report ' + paramChips +
            ' for the row that was clicked, so the sub-report shows exactly that record. The links work in the ' +
            'SSRS viewer <b>and</b> in a PDF exported from it.</div>'
        : '<div class="burst-callout">The links work in the SSRS viewer <b>and</b> in a PDF exported from it.</div>') +
      // ---- collapsible step-by-step ----
      '<details class="o2s-details"><summary>Show me the exact steps: build, upload, test</summary>' +
        '<div class="burst-files-sub" style="margin-top:10px">1 · BUILD THEM HERE</div>' +
        '<ol class="burst-steps">' +
          '<li><b>The main report is done</b> — this tool read its links and listed each sub-report below.</li>' +
          '<li><b>Build each sub-report</b> — drop its files (Oracle <code>.xml</code>, an existing <code>.rdl</code>, ' +
            'or <code>.sql</code>/<code>.docx</code>) into its slot. This tool builds it and declares the exact ' +
            'values the main report will hand over.</li>' +
          '<li><b>Download the main report again</b> — if a sub-report turns out to have a different name from ' +
            'the one the link guessed, this tool corrects the main report for you and says “Parent re-synced”. ' +
            'Download the main <code>.rdl</code> again from the <b>RDL XML</b> view, and each sub-report from its ' +
            'card. <i>(Main, then sub-reports, then the main once more.)</i></li>' +
        '</ol>' +
        '<div class="burst-files-sub" style="margin-top:12px">2 · UPLOAD TO SSRS — sub-reports first</div>' +
        '<ol class="burst-steps">' +
          '<li><b>Upload the sub-report(s) first</b>, into the <b>same folder</b> as the main report, under the ' +
            '<b>exact name</b> shown on each card. SSRS looks the link up by that bare name at the moment ' +
            'someone clicks it, so the sub-report has to be there already.</li>' +
          '<li><b>Upload the main report second.</b></li>' +
          '<li><b>Test it</b> — open the main report in SSRS, click one row’s link, then click ' +
            '<b>“generate all”</b> and check the order.</li>' +
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
  const detected = children.filter(c => c.detected).length;
  // The personalized drill-through guide narrates THIS report's links --
  // with zero detected links it would narrate a feature the report does
  // not have, so it only renders when links exist. The manual "+ Add"
  // affordance lives here in the tab (the sidebar dropzone section is
  // reserved for detected links).
  const guide = detected > 0 ? _subDeployGuideHTML(children) : "";
  // The button used to explain itself in a title= tooltip, which only opens
  // for a mouse. The sentence is on screen instead -- as the hint beside the
  // button, or, in the empty state, as the line that already says it.
  const addRow = (hint) =>
    '<div class="subreport-add-row">' +
      '<button id="subreport-add-manual" class="btn btn-ghost" type="button">' +
        '+ Add a sub-report</button>' +
      (hint ? '<span class="subreport-add-hint">Build a sub-report on ' +
              'its own, from any files you have.</span>' : "") +
    '</div>';
  if (!children.length) {
    host.innerHTML =
      '<div class="subreport-empty-state">No row in this report opens ' +
      'another report, so there is nothing to build automatically. You can ' +
      'still build a sub-report on its own, from any files you have:</div>' +
      addRow(false);
  } else {
    host.innerHTML = guide +
      children.map((c, i) => _subCardHTML(c, i)).join("") + addRow(true);
    _subWireCards(host);
  }
  const addBtn = document.getElementById("subreport-add-manual");
  if (addBtn) addBtn.addEventListener("click", subAddManual);
}

function _subCardHTML(c, idx) {
  const child = escHtml(c.name);
  const ln = c.link || {};
  const meta = [];
  if (c.detected) {
    if (ln.link_text)    meta.push("The link reads: <b>" + escHtml(ln.link_text) + "</b>");
    if (ln.parent_field) meta.push("Link sits on: <code>" + escHtml(ln.parent_field) + "</code>");
    if (ln.url_formula)  meta.push("Address built from: <code>" + escHtml(ln.url_formula) + "</code>");
    const binds = (ln.bind_params || []).map(escHtml).join(", ");
    if (binds) meta.push("Values handed over: <code>" + binds + "</code>");
  } else {
    meta.push('<span class="subreport-empty">you added this one yourself</span>');
  }
  const arts = (c.artifacts || []).map(a => '<span class="chip">' + escHtml(a) + "</span>").join(" ")
    || '<span class="subreport-empty">no files dropped in yet</span>';

  const built = (state.subreportBuilds || {})[c.name];
  let preview = "";
  if (built) {
    const srcLabel = ({ oracle_xml: "an Oracle XML export", rdl: "an existing .rdl",
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
          ? '<details class="subreport-issues"><summary>Things to check (' + (built.issues || []).length +
            ")</summary><ul>" + issues + "</ul></details>"
          : "") +
        '<details class="subreport-rdl"><summary>The report file this made</summary>' +
          '<pre class="code-block"><code class="language-xml">' +
          escHtml(built.rdl_xml || "") + "</code></pre></details>" +
      "</div>";
  }

  return (
    '<div class="subreport-card" data-child="' + child + '">' +
      '<div class="subreport-head">' +
        '<h3 class="subreport-title">Sub-report: ' + child + "</h3>" +
        '<span class="subreport-num">' +
          (c.detected ? "link #" + (idx + 1) : "added by you") + "</span>" +
      "</div>" +
      '<div class="subreport-meta">' + meta.join(" &middot; ") + "</div>" +
      '<div class="subreport-artifacts">Files dropped in: ' + arts + "</div>" +
      '<div class="subreport-label-row">' +
        '<label class="subreport-label-lbl">Display name / size' +
          '<input type="text" class="conn-input subreport-label" data-child="' + child +
          '" placeholder="e.g. Standard 12 x 9 Envelope" autocomplete="off" spellcheck="false">' +
        "</label>" +
        '<div class="muted-note">These are the words the link shows on the main ' +
          'report. If you name an envelope size here (for example <code>12 x 9</code>), ' +
          'the sub-report is built at that page size. This browser remembers what ' +
          'you type.</div>' +
      "</div>" +
      '<div class="subreport-actions">' +
        // `hidden` on the input took this control OUT of the tab order
        // entirely: the visible "Add artifact(s)" button was mouse-only.
        // Clipped instead of hidden keeps it focusable; the wrapping label
        // names it and .subreport-add:focus-within draws the ring.
        '<label class="btn btn-ghost subreport-add">Add files' +
          '<input type="file" multiple class="subreport-upload file-input-hidden" data-child="' +
          child + '"></label>' +
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
  // Only this tab's blocks — highlightAll() would re-tokenize the multi-MB
  // RDL/side-by-side blocks too (seconds of frozen UI on real reports).
  highlightPanel(host);
}

// ---- Shared upload + build flow (used by sidebar slots and tab cards) ----
async function subUploadArtifacts(child, files, msgEl) {
  // Same glyph + word + sentence as every other status line in the app, so
  // "uploaded" and "failed" can never look identical again.
  const setMsg = (t, st) => setInlineStatus(msgEl, st || "working", t);
  const subTooBig = uploadTooLargeMessage(files);
  if (subTooBig) {
    setMsg(subTooBig, "failed");
    statusFail("Could not add artifacts to the sub-report " + child,
               { why: subTooBig, subject: child });
    return;
  }
  try {
    setMsg("Uploading " + files.length + " file(s)…");
    const fd = new FormData();
    files.forEach(f => fd.append("artifact", f, f.name));
    const r = await postFormOrExplain(
      "/api/subreport/" + encodeURIComponent(child) + "/upload", fd,
      estimateUploadBytes(files));
    const j = await safeJson(r);
    if (!r.ok || j.error) throw new Error(j.error || "upload failed");
    const c = _subFindChild(child);
    if (c) c.artifacts = j.artifacts || [];
    renderSubreportSidebar();
    setMsg("Building…");
    await subBuildAndPreview(child, { activate: true, msgEl: msgEl });
  } catch (err) {
    const why = (err && err.message) || String(err);
    setMsg(why, "failed");
    statusFail("Could not add artifacts to the sub-report " + child,
               { why: why, subject: child,
                 next: "Check the files are the child report's Oracle XML, "
                     + "an .rdl, or its SQL, then drop them again." });
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
  const setMsg = (t, st) => setInlineStatus(opts.msgEl, st || "working", t);
  setMsg("Building… this line stays here until it finishes.");
  statusBegin(child, "Building the sub-report");
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
    _ensureSubTabReachable();
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
      setMsg("Built. Parent re-synced — re-download the parent .rdl too.",
             "done");
      statusDone("Built the sub-report " + child, { subject: child,
        lines: ["Do this next: download the parent .rdl AGAIN. Its link now "
                + "points at this child, so the copy you downloaded earlier "
                + "is out of date."] });
    } else {
      setMsg("Built — preview ready.", "done");
      statusDone("Built the sub-report " + child, { subject: child,
        lines: ["Do this next: deploy this child report to SSRS BEFORE the "
                + "parent, under this exact name, or the parent's link will "
                + "not open anything."] });
    }
    toast("Sub-report " + child + " generated", "ok");
    return j;
  } catch (err) {
    // The reason used to be written only into the card while the toast --
    // the thing that grabs the eye -- said "Sub-report build failed" and
    // nothing else.
    const why = (err && err.message) || String(err);
    setMsg(why, "failed");
    statusFail("Could not build the sub-report " + child,
               { why: why, subject: child,
                 next: "Open the Sub-Reports view: the card for " + child
                     + " lists the files it has so far. It needs that "
                     + "report's Oracle XML, an existing .rdl, or its SQL." });
    return null;
  }
}

// Make the Sub-Reports tab reachable right now: unhide it, and when it is
// demoted to the Advanced group (no detected links), reveal that group so
// activating the tab never lands on a panel with an invisible button.
function _ensureSubTabReachable() {
  const tabBtn = document.getElementById("tabbtn-subreports");
  if (!tabBtn) return;
  tabBtn.hidden = false;
  if (tabBtn.classList.contains("tab-adv")) setAdvancedTabs(true);
}

function subAddManual() {
  const raw = (window.prompt("Name for the sub-report (e.g. CHILD_REPORT):") || "").trim();
  if (!raw) return;
  const name = raw.replace(/[^A-Za-z0-9_-]/g, "_");
  state.subreportChildren = state.subreportChildren || [];
  if (!state.subreportChildren.some(c => c.name === name)) {
    state.subreportChildren.push({ name: name, detected: false, link: null, artifacts: [] });
  }
  _ensureSubTabReachable();
  renderSubreportsTab();
  renderSubreportSidebar();
  activateTab("subreports");
}


// "Copy failed" said nothing: not why, and not what to do instead. The two
// real causes are a page that is not on https/localhost and a switched-off
// clipboard permission, and in both cases the user CAN still copy by hand.
function clipboardRefused() {
  statusFail("Could not copy to the clipboard", {
    kind: "clipboard_blocked",
    why: "The browser refused clipboard access. That happens when the page "
       + "is not on https or localhost, or when clipboard permission is "
       + "switched off for this site.",
    next: "Select the text in the panel and press Ctrl+C instead." });
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

  // Re-read the server's upload cap on load. The page already carries it as a
  // data attribute (so the very first drop is checked), but a server restarted
  // with a different O2S_MAX_UPLOAD_MB behind a stale tab would otherwise
  // pre-flight against the old number.
  refreshUploadLimit().catch(() => { /* offline: the data attribute stands */ });

  // Tabs. Click activates; the arrow keys MOVE within the strip and Home/End
  // jump to its ends (ARIA tabs pattern). Without this the roving tabindex
  // would leave eight of the nine tabs unreachable by keyboard.
  $$(".tab").forEach(t => {
    t.addEventListener("click", (e) => {
      e.preventDefault();
      console.log("[Oracle2SSRS] tab clicked:", t.dataset.tab);
      activateTab._userClicked = true;   // enables the no-data hint toast
      activateTab(t.dataset.tab);
    });
    t.addEventListener("keydown", (e) => {
      const KEYS = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 1, ArrowUp: -1 };
      const tabs = visibleTabs();
      if (!tabs.length) return;
      let target = null;
      if (e.key in KEYS) {
        const i = tabs.indexOf(t);
        target = tabs[(i + KEYS[e.key] + tabs.length) % tabs.length];
      } else if (e.key === "Home") {
        target = tabs[0];
      } else if (e.key === "End") {
        target = tabs[tabs.length - 1];
      }
      if (!target) return;
      e.preventDefault();
      activateTab._userClicked = true;
      activateTab(target.dataset.tab);
      target.focus();
    });
  });

  // Drop zone
  const dropZone = $("#drop-zone");
  const fileInput = $("#file-input");
  const filesInput = $("#file-input-files");
  const pickLink = $("#pick-files-link");

  if (dropZone) {
    if (fileInput) {
      // The drop zone is a real <button> now, so Enter and Space already
      // fire this click. A keydown handler of our own would open the file
      // picker TWICE on every keyboard activation.
      dropZone.addEventListener("click", () => { fileInput.click(); });
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
      } catch (e) { clipboardRefused(); }
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
      if (!state.data) { _needAReportFirst(); return; }
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
  // (The "+ Add a sub-report" button lives in the Sub-Reports tab and is
  // wired by renderSubreportsTab each render -- not here.)
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
  setInlineStatus(status, "working", "Updating the preview…");
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
      setInlineStatus(status, "done", "Preview updated with your settings.");
    } else {
      const why = (resp && resp.error)
        || "The server answered, but not with a preview.";
      setInlineStatus(status, "failed", why);
      statusFail("Could not update the distribution preview",
        { why: why, subject: "Distribution settings",
          next: "Check the Email-Source SQL box below for a typo, then press "
              + "Update Preview again." });
    }
  }).catch((err) => {
    const why = (err && err.message) || String(err);
    setInlineStatus(status, "failed", why);
    statusFail("Could not update the distribution preview",
      { why: why, subject: "Distribution settings" });
  });
}

function _burstPackDownload(overrides) {
  const status = document.getElementById("bf-status");
  setInlineStatus(status, "working",
    "Building the burst pack… no percentage for this step; the server "
    + "answers when the .zip is ready.");
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
      setInlineStatus(status, "done", "Downloaded " + nm + ".");
      statusDone("Downloaded the burst pack " + nm,
        { subject: "Burst pack",
          lines: ["Do this next: unzip it on the report server and follow the "
                  + "steps in the README inside."] });
    });
  }).catch((err) => {
    // "Downloaded burst_pack.zip." and "Download failed:" used to be the same
    // grey 11.5px line with no icon and no word -- success and failure looked
    // identical.
    const why = (err && err.message) || String(err);
    setInlineStatus(status, "failed", why);
    statusFail("Could not build the burst pack",
      { why: why, subject: "Burst pack",
        next: "Nothing was downloaded. Press Update Preview first: if that "
            + "fails too, the Email-Source SQL is the thing to fix." });
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
  // Same relevance filter as the main dropzone: "add more artifacts" is the
  // other way a 35 MB reference PDF gets picked up.
  var part = partitionUploadList(newFiles);
  var skipLine = describeSkipped(part.skipped, part.skippedBytes);
  if (skipLine) toast(skipLine, 'ok');
  newFiles = part.send;
  if (!newFiles.length) {
    var why = skipLine || 'Nothing selected is a conversion input.';
    // Same pre-flight, same reason it cannot point at an Ingest Summary.
    statusFail('Nothing you picked can be added', { why: why,
      kind: 'reference_files_only' });
    setStatus('Nothing to add', 'err', why);
    toast(why, 'err');
    return Promise.resolve();
  }
  var enrichTooBig = uploadTooLargeMessage(newFiles);
  if (enrichTooBig) {
    statusFail('Those extra artifacts are too big to send',
      { why: enrichTooBig, kind: 'upload_too_large' });
    setStatus('Upload too large', 'err', enrichTooBig);
    toast(enrichTooBig, 'err');
    return Promise.resolve();
  }
  statusBegin(newFiles.length + ' new artifact'
              + (newFiles.length === 1 ? '' : 's'),
              'Converting again with the extra artifacts included');
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
  return postFormOrExplain('/api/convert-bundle', fd, estimateUploadBytes(newFiles))
    .then(function(res) { return safeJson(res).then(function(json) { return {res:res, json:json}; }); })
    .then(function(rj) {
      var res = rj.res, json = rj.json;
      if (!res.ok) throw apiError(json, res, 'Bundle re-conversion failed');
      if (json.error && json.error !== 'no_convertible_artifacts') {
        throw apiError(json, res, 'Bundle re-conversion failed');
      }
      onConverted(json);
      toast('Bundle re-converted with new artifacts', 'ok');
    })
    .catch(function(err) {
      console.error('[Oracle2SSRS] enrich re-convert failed:', err);
      var m = (err && err.message) || 'Failed to add artifacts';
      statusFail('Could not re-convert with the extra artifacts',
                 { why: m, kind: errorKind(err),
                   next: 'The report you already had is unchanged and still '
                       + 'downloadable. Try adding the files again, or add '
                       + 'them one at a time to find the one it cannot read.' });
      setStatus('Error', 'err', m);
      toast(m, 'err');
    });
}

