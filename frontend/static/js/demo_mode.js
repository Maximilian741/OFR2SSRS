/* =========================================================
   Oracle2SSRS — guided tour ("How this tool works")
   A step-by-step wizard that converts a sample and walks
   every view, explaining what each one is FOR in plain
   words. No framework; safe to load on any page state.
   ========================================================= */
(function () {
  "use strict";

  function $(s) { return document.querySelector(s); }
  function $$(s) { return Array.from(document.querySelectorAll(s)); }

  // ---------- styles ----------
  function ensureStyles() {
    if ($("#demo-tour-styles")) return;
    const st = document.createElement("style");
    st.id = "demo-tour-styles";
    st.textContent = [
      // The ring is a UI indicator, so it owes 3:1 against whatever it is
      // drawn over. --tour-ring is the token that guarantees that in BOTH
      // themes; the old hardcoded amber managed 1.99:1 on white.
      ".demo-highlight{outline:3px solid var(--tour-ring); outline-offset:3px;",
      "  border-radius:6px; transition:outline .15s;}",
      ".demo-tooltip{position:fixed; z-index:99999; max-width:400px;",
      "  background:var(--tour-bg); color:var(--tour-ink);",
      "  border:1px solid var(--tour-line);",
      "  border-radius:10px; padding:14px 16px; font-size:13.5px;",
      "  line-height:1.55; box-shadow:0 12px 40px rgba(0,0,0,.45);}",
      ".demo-tooltip h4{margin:0 0 6px; font-size:14px; color:var(--tour-title);}",
      ".demo-tooltip .demo-step-n{opacity:.65; font-weight:normal;}",
      ".demo-tooltip .demo-btns{margin-top:12px; display:flex; gap:8px;",
      "  justify-content:flex-end;}",
      ".demo-tooltip button{border:0; border-radius:6px; padding:6px 14px;",
      "  font-size:12.5px; cursor:pointer;}",
      ".demo-tooltip .demo-next{background:var(--tour-cta); color:var(--tour-cta-ink);",
      "  font-weight:bold;}",
      ".demo-tooltip .demo-skip{background:transparent; color:var(--tour-muted);}",
      // The KEYBOARD ring inside the card. The card is pinned dark in both
      // themes, but the page's --focus-ring follows the theme, so in light
      // mode the ring around "Next" was a light-theme colour on a dark card:
      // 2.76:1, under the 3:1 that WCAG 2.2 SC 1.4.11 asks of a focus
      // indicator -- and this is the ONE control the tour asks you to press.
      // --tour-focus is pinned with the rest of the card (5.67:1 on it).
      // The card's own outer edge is not touched: a ring there is drawn on
      // the page, where the theme's ring is the right colour.
      ".demo-tooltip :focus-visible{outline:2px solid var(--tour-focus);",
      "  outline-offset:2px; border-radius:6px;}",
      "@supports not selector(:focus-visible){",
      "  .demo-tooltip :focus{outline:2px solid var(--tour-focus);",
      "    outline-offset:2px; border-radius:6px;} }",
    ].join("\n");
    document.head.appendChild(st);
  }

  // ---------- tour plumbing ----------
  let _hl = null;
  function highlight(node) {
    if (_hl) _hl.classList.remove("demo-highlight");
    _hl = node || null;
    if (_hl) _hl.classList.add("demo-highlight");
  }

  function waitFor(predicate, timeoutMs, intervalMs) {
    timeoutMs = timeoutMs || 8000;
    intervalMs = intervalMs || 120;
    return new Promise(resolve => {
      const t0 = Date.now();
      (function check() {
        let ok = false;
        try { ok = !!predicate(); } catch (e) { /* keep polling */ }
        if (ok) return resolve(true);
        if (Date.now() - t0 > timeoutMs) return resolve(false);
        setTimeout(check, intervalMs);
      })();
    });
  }

  // Shows a tooltip near `target`; resolves "next" or "skip".
  //
  // The tooltip is a DIALOG, not a floating div: the tour is the primary
  // teaching surface, and before this it had no role, took no focus, and
  // could not be closed from the keyboard. Reaching "Next" meant tabbing
  // through the entire page underneath it.
  function step(target, stepNo, total, title, html) {
    return new Promise(resolve => {
      document.querySelectorAll(".demo-tooltip").forEach(n => n.remove());
      highlight(target);
      if (target && target.scrollIntoView) {
        // A tour that yanks the page around is unusable for anyone who
        // asked the system for less motion.
        const still = window.matchMedia
          && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        target.scrollIntoView({ behavior: still ? "auto" : "smooth",
                                block: "center" });
      }
      const tip = document.createElement("div");
      tip.className = "demo-tooltip";
      tip.setAttribute("role", "dialog");
      tip.setAttribute("aria-modal", "true");
      tip.setAttribute("tabindex", "-1");
      tip.setAttribute("aria-label",
        "Guided tour, step " + stepNo + " of " + total + ": " + title);
      tip.innerHTML =
        "<h4><span class='demo-step-n'>" + stepNo + "/" + total + "</span> " +
        title + "</h4><div>" + html + "</div>" +
        "<div class='demo-btns'>" +
        "<button class='demo-skip' type='button'>End tour</button>" +
        "<button class='demo-next' type='button'>Next →</button></div>";
      document.body.appendChild(tip);
      // position: below-right of target, clamped to viewport
      const r = target ? target.getBoundingClientRect()
                       : { left: 40, bottom: 40, top: 40 };
      const w = Math.min(400, window.innerWidth - 32);
      let x = Math.min(Math.max(12, r.left), window.innerWidth - w - 12);
      let y = r.bottom + 12;
      if (y + 220 > window.innerHeight) y = Math.max(12, r.top - 240);
      tip.style.left = x + "px";
      tip.style.top = y + "px";
      // Now clamp against the box that actually exists. The guess above
      // uses the target's rect BEFORE scrollIntoView has moved anything and
      // assumes the step is 220px tall; measured, a step pointed at the
      // sidebar summary landed BELOW the fold, where the words could not be
      // read and "Next" could not be clicked. Focus still went there, so
      // the tour looked broken rather than gone. Nothing that teaches may
      // sit off screen.
      const box = tip.getBoundingClientRect();
      const maxTop = Math.max(12, window.innerHeight - box.height - 12);
      const maxLeft = Math.max(12, window.innerWidth - box.width - 12);
      tip.style.top = Math.min(Math.max(12, y), maxTop) + "px";
      tip.style.left = Math.min(Math.max(12, x), maxLeft) + "px";

      const nextBtn = tip.querySelector(".demo-next");
      const skipBtn = tip.querySelector(".demo-skip");
      const done = (how) => {
        document.removeEventListener("keydown", onKey, true);
        resolve(how);
      };
      nextBtn.addEventListener("click", () => done("next"));
      skipBtn.addEventListener("click", () => done("skip"));

      // Focus moves INTO the step, so "Next" is one keystroke away instead
      // of thirty; Escape ends the tour, which is what every other dialog
      // on this page does; Tab cycles between the two buttons and cannot
      // wander into the page the tour is pointing at.
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); done("skip"); return; }
        if (e.key !== "Tab") return;
        const items = [skipBtn, nextBtn];
        const i = items.indexOf(document.activeElement);
        e.preventDefault();
        const j = i === -1 ? 0
          : (i + (e.shiftKey ? -1 : 1) + items.length) % items.length;
        items[j].focus();
      }
      document.addEventListener("keydown", onKey, true);
      nextBtn.focus();
    });
  }

  function cleanup(returnTo) {
    highlight(null);
    document.querySelectorAll(".demo-tooltip").forEach(n => n.remove());
    // Focus must not be left on a node we just deleted -- that drops it to
    // <body> and the next Tab restarts at the top of the document.
    if (returnTo && returnTo.focus && document.contains(returnTo)) {
      returnTo.focus();
    }
  }

  function clickTab(name) {
    const t = $('.tab[data-tab="' + name + '"]');
    if (t) t.click();
    return t;
  }

  // The last conversion payload — captured off the o2s:converted event so
  // the tour can ADAPT its steps to what the converted report actually
  // contains (bursting signal, drill-through links) instead of narrating
  // features this report does not have.
  let _lastData = null;

  // Show/hide the advanced tab group, via the app's own helper when
  // available (it keeps the toggle button label in sync).
  function setAdvanced(on) {
    if (typeof window.o2sSetAdvanced === "function") {
      window.o2sSetAdvanced(on);
    } else {
      document.body.classList.toggle("show-advanced", !!on);
    }
  }

  // ---------- the tour ----------
  async function runTour() {
    // Which optional steps will actually run is known BEFORE the first
    // tooltip, so the counter can tell the truth. It used to be hardcoded
    // to 13 while the sample step silently bumped the number when a
    // conversion already existed -- the user then watched it count
    // 1, 3, 4, 5 and had no idea what happened to step 2.
    const chips = $$("#samples-list .sample-chip");
    const willConvertSample = !!(chips.length && !_lastData);
    const total = willConvertSample ? 13 : 12;
    let n = 0;
    const next = async (target, title, html) => {
      n += 1;
      const r = await step(target, n, total, title, html);
      if (r === "skip") throw { tourEnded: true };
    };
    // Remember whether the advanced group was open before the tour so we
    // can restore the user's own view state afterwards -- and where focus
    // was, so ending the tour returns the keyboard to where it started.
    const advWasOpen = document.body.classList.contains("show-advanced");
    const focusWasOn = document.activeElement;

    try {
      await next($("#drop-zone"), "Start here: drop an Oracle report",
        "This tool takes a report exported from <b>Oracle Reports</b> and " +
        "writes the same report for <b>SSRS</b> — Microsoft's reporting " +
        "server. What you get back is an <b>.rdl</b> file you can upload. " +
        "Drag one export here, or the whole folder of files that came with " +
        "it. Nothing leaves this computer: the conversion runs here.");

      // Convert a sample so every later view has real content.
      if (willConvertSample) {
        await next(chips[0], "First, a sample report",
          "I'll click <b>" + (chips[0].textContent.trim() || "a sample") +
          "</b> so every view has something in it. Converting a real " +
          "report takes about a second.");
        let fired = false;
        document.addEventListener("o2s:converted", () => { fired = true; },
          { once: true });
        try { chips[0].click(); } catch (e) { /* tolerated */ }
        await waitFor(() => fired ||
          ($("#mockup-host") && $("#mockup-host").children.length > 0), 12000);
      }

      await next($("#summary-section") || $("#sidebar"),
        "Read this before you trust anything",
        "This panel lists what was found: queries, parameters and formulas. " +
        "Two lines matter most.<br>• The <b>verdict</b> — READY, AMBER, RED " +
        "or BLOCKER — with the exact reasons.<br>• The <b>fidelity</b> " +
        "percentage — how much of the original came across. 100% means no " +
        "column, parameter or layout field was quietly dropped.<br>" +
        "This tool tells you when something will not work, before you " +
        "deploy it.");

      await next(clickTab("mockup"), "Preview — what the report will look like",
        "A page-for-page preview, filled with sample data. The two buttons " +
        "above it switch what you see: <b>Frontend</b> shows the printed " +
        "pages; <b>Backend</b> shows the empty layout, so you can see which " +
        "data field sits in which box. Formatting rules, seals and logos, " +
        "letters, invoices — even buttons printed inside the report, like a " +
        "<i>Send Emails</i> button — all show up here the way Oracle " +
        "printed them.");

      await next(clickTab("rdl"), "RDL XML — the file you upload",
        "The report file this tool wrote. This is what you put on the " +
        "server. A very long file shows only its beginning here, for speed; " +
        "the <b>download always contains the whole file</b>. You rarely need " +
        "to read this — it is here so you can check anything you want to.");

      // The remaining views live in the Advanced group — reveal it so the
      // highlighted tab buttons are actually visible while we tour them.
      setAdvanced(true);

      await next(clickTab("side"), "Side-by-Side — check the translation",
        "The Oracle file on the left, the new file on the right. Search " +
        "both for a column or query name to answer <i>“where did this end " +
        "up?”</i> This is your record when someone asks how a number got " +
        "onto the page.");

      await next(clickTab("live"), "Live Data — prove the SQL runs",
        "This runs the report's SQL against a small read-only " +
        "<b>sample database</b> that ships with this tool. Tables the " +
        "sample database does not have come back empty. It proves the SQL " +
        "is valid and runs; it does <b>not</b> touch your database. The " +
        "connection you type on the left is baked into the downloaded RDL " +
        "for the server to use instead.");

      await next(clickTab("validate"), "Validation — every finding, explained",
        "Every check behind the verdict: dropped filters, SQL built while " +
        "the report runs, image bindings, file-format rules. Each finding " +
        "says what happens when the report runs and what to do about it. " +
        "<b>BLOCKER</b> = do not upload yet. <b>RED</b> = it uploads, but " +
        "something will come out wrong. <b>AMBER</b> = worth reading. " +
        "<b>READY</b> = nothing is in your way.");

      await next(clickTab("deploy"), "Deploy Checklist — the steps for going live",
        "Where the shared data source goes, how to upload, and why you must " +
        "never click <b>Refresh Fields</b> in Report Builder — the field " +
        "list is already complete, and refreshing it makes Report Builder " +
        "ask you for every parameter. Type your shared data source and " +
        "report server address in the panel on the left and they go into " +
        "every file you download.");

      await next(clickTab("extras"), "Extras — coverage and second opinions",
        "How much of the original came across and what still needs a " +
        "person, a record of the decisions made while converting, and " +
        "ready-made questions you can paste into your own AI tool if you " +
        "want a second opinion on a query or a formula.");

      // The Bursting and Sub-Reports tabs are STRUCTURE-DRIVEN: each is a
      // main tab only when the converted report carries its signal, and
      // sits in the Advanced group otherwise. Adapt the narration to what
      // THIS report actually contains.
      const d = _lastData || {};
      const isBursting = !!(d.bursting && d.bursting.is_bursting);
      const nLinks = ((d.subreport_links) || []).length;

      if (isBursting) {
        await next(clickTab("burst"), "Bursting — one run, one PDF per person",
          "In Oracle this report went out per recipient, so Bursting is a " +
          "main view here. It builds the pack that splits one run into one " +
          "PDF per person and emails each one, using your service account. " +
          "This is what replaces Oracle's <i>distribute=YES</i>.");
      } else {
        await next(clickTab("burst"), "Bursting — this report does not use it",
        "This report runs once and prints one set of pages: nothing in it " +
        "sends a copy per recipient. So Bursting sits under <b>Advanced " +
        "views</b>. When a report does burst — one PDF per person, emailed " +
        "automatically — this view moves out in front and builds the pack " +
        "for you.");
      }

      if (nLinks) {
        await next(clickTab("subreports"), "Sub-Reports — reports this one opens",
          "A row in this report can open a second report about that row — " +
          "an envelope, a detail page. Those are found automatically and " +
          "listed here. Drop a second report's files in and it is built the " +
          "same way this one was. Upload the second report first: the link " +
          "is matched by name at the moment someone clicks it.");
      } else {
        await next(clickTab("subreports"), "Sub-Reports — none in this report",
          "No row in this report opens another report, so Sub-Reports sits " +
          "under <b>Advanced views</b>. When a report does link out, the " +
          "linked reports are found and built here — and <b>+ Add a " +
          "sub-report</b> builds one from any files you have.");
      }

      await next($("#advanced-toggle") || $(".tab[data-tab='mockup']"),
        "That is the whole flow",
        "<b>Drop it in → read the verdict → check the preview → download → " +
        "upload.</b> The rest of the views live behind this button, " +
        "including any view this report does not use. The verdicts are " +
        "honest: READY means the checks found nothing that would stop the " +
        "report uploading or running, and RED tells you exactly what will " +
        "go wrong. What no check can tell you is whether the page looks " +
        "like your original — open <b>Preview</b> and compare that " +
        "yourself.");
    } catch (e) {
      if (!e || !e.tourEnded) throw e;
    } finally {
      cleanup(focusWasOn);
      // Restore the user's own view state: the tour revealed the advanced
      // group for its walkthrough; don't leave it open unless it was.
      setAdvanced(advWasOpen);
      clickTab("mockup");
      // The tour used to end in a three-second toast. Someone who looked
      // away while it finished had no way to find out that it HAD finished,
      // or that the view had been put back. The app's own status surface
      // keeps the message on screen instead.
      const lines = [
        "You are back on the Preview view, and the views you had open " +
        "before the tour have been put back.",
        "Do this next: open “How it works” at the top of the page to run " +
        "the tour again, or drop your own Oracle report on the left.",
      ];
      if (window.o2sStatus && typeof window.o2sStatus.done === "function") {
        window.o2sStatus.done("Guided tour finished",
                              { subject: "", lines: lines });
      } else if (typeof window.toast === "function") {
        window.toast("Guided tour finished.", "ok");
      }
    }
  }

  // ---------- entry points ----------
  function makeButton() {
    // The "How this tool works" button in the topbar starts the tour.
    const host = $("#how-it-works-btn") || $("#tour-btn");
    if (host && !host._tourWired) {
      host._tourWired = true;
      host.addEventListener("click", (e) => {
        e.preventDefault();
        runTour();
      });
    }
  }

  function init() {
    ensureStyles();
    makeButton();
    // Track every conversion so the tour adapts to the CURRENT report's
    // structure (and knows whether a conversion already happened).
    document.addEventListener("o2s:converted", function (e) {
      _lastData = (e && e.detail) || null;
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose for the empty-state hero's inline button, if present.
  window.o2sRunTour = runTour;
})();
