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
      ".demo-highlight{outline:3px solid #f6a821; outline-offset:3px;",
      "  border-radius:6px; transition:outline .15s;}",
      ".demo-tooltip{position:fixed; z-index:99999; max-width:400px;",
      "  background:#1d2733; color:#f2f6fa; border:1px solid #3d4c5e;",
      "  border-radius:10px; padding:14px 16px; font-size:13.5px;",
      "  line-height:1.55; box-shadow:0 12px 40px rgba(0,0,0,.45);}",
      ".demo-tooltip h4{margin:0 0 6px; font-size:14px; color:#ffd479;}",
      ".demo-tooltip .demo-step-n{opacity:.65; font-weight:normal;}",
      ".demo-tooltip .demo-btns{margin-top:12px; display:flex; gap:8px;",
      "  justify-content:flex-end;}",
      ".demo-tooltip button{border:0; border-radius:6px; padding:6px 14px;",
      "  font-size:12.5px; cursor:pointer;}",
      ".demo-tooltip .demo-next{background:#f6a821; color:#1d2733;",
      "  font-weight:bold;}",
      ".demo-tooltip .demo-skip{background:transparent; color:#9fb0c1;}",
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
  function step(target, stepNo, total, title, html) {
    return new Promise(resolve => {
      document.querySelectorAll(".demo-tooltip").forEach(n => n.remove());
      highlight(target);
      if (target && target.scrollIntoView) {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      const tip = document.createElement("div");
      tip.className = "demo-tooltip";
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
      tip.querySelector(".demo-next").addEventListener("click",
        () => resolve("next"));
      tip.querySelector(".demo-skip").addEventListener("click",
        () => resolve("skip"));
    });
  }

  function cleanup() {
    highlight(null);
    document.querySelectorAll(".demo-tooltip").forEach(n => n.remove());
  }

  function clickTab(name) {
    const t = $('.tab[data-tab="' + name + '"]');
    if (t) t.click();
    return t;
  }

  // ---------- the tour ----------
  async function runTour() {
    const total = 13;
    let n = 0;
    const next = async (target, title, html) => {
      n += 1;
      const r = await step(target, n, total, title, html);
      if (r === "skip") throw { tourEnded: true };
    };

    try {
      await next($("#drop-zone"), "Drop an Oracle report here",
        "This tool converts <b>Oracle Reports XML</b> into a ready-to-deploy " +
        "<b>SSRS report (.rdl)</b>. Drag a single XML export here — or a " +
        "whole folder of artifacts (XML + images + related files). " +
        "Nothing leaves this machine: conversion runs locally.");

      // Convert a sample so every later view has real content.
      const chips = $$("#samples-list .sample-chip");
      if (chips.length && !window.state?.data) {
        await next(chips[0], "We'll convert a sample now",
          "Clicking <b>" + (chips[0].textContent.trim() || "a sample") +
          "</b> so you can see every view populated. A real conversion " +
          "takes about a second.");
        let fired = false;
        document.addEventListener("o2s:converted", () => { fired = true; },
          { once: true });
        try { chips[0].click(); } catch (e) { /* tolerated */ }
        await waitFor(() => fired ||
          ($("#mockup-host") && $("#mockup-host").children.length > 0), 12000);
      } else {
        n += 1; // keep numbering stable when data already present
      }

      await next($("#summary-section") || $("#sidebar"),
        "The honesty panel — read this first",
        "The sidebar shows what was found (queries, parameters, formulas) " +
        "plus two <b>honesty signals</b>:<br>• the <b>verdict banner</b> — " +
        "READY / AMBER / RED / BLOCKER, with the exact reasons;<br>• the " +
        "<b>fidelity score</b> — 1.00 means no column, parameter or layout " +
        "field was silently lost.<br>This tool tells you when something " +
        "will NOT work — before you deploy it.");

      await next(clickTab("mockup"), "HTML Mockup — what the report looks like",
        "A pixel-faithful preview filled with sample data. The " +
        "<b>Frontend/Backend toggle</b> switches between the filled-in view " +
        "and the skeleton (labels + bindings). Conditional formatting, " +
        "seals/logos, letters, invoices — even <b>in-report action " +
        "buttons</b> (like a mass-email <i>Send Emails</i> button) render " +
        "here exactly as Oracle printed them.");

      await next(clickTab("rdl"), "RDL XML — the actual deliverable",
        "The generated SSRS report definition. This is what you deploy. " +
        "Very large documents show the first chunk here for speed — the " +
        "<b>download always carries the complete file</b>. You rarely need " +
        "to read this; it's here for transparency and diffing.");

      await next(clickTab("side"), "Side-by-side — audit the translation",
        "Oracle source XML on the left, generated RDL on the right. Use it " +
        "to answer <i>“where did this field/query end up?”</i> — search " +
        "both panes for a column name and compare. This is your audit " +
        "trail when someone asks how a value got there.");

      await next(clickTab("live"), "Live data — test queries before deploy",
        "Runs the report's REAL queries against your database (enter a " +
        "connection string in the sidebar first). Fill in parameter values " +
        "and click <b>Run query</b> — you see exactly the rows SSRS will " +
        "see. Use this to prove the SQL works <i>before</i> touching the " +
        "report server.");

      await next(clickTab("validate"), "Validation — every issue, explained",
        "The full pre-flight audit behind the verdict banner: dropped " +
        "filters, runtime SQL splices, image bindings, schema rules. Each " +
        "finding says what it means <i>at run time</i> and what to do. " +
        "BLOCKER = will not work; RED = wrong output; AMBER = check this; " +
        "READY = deploy it.");

      await next(clickTab("deploy"), "Deployment — the go-live checklist",
        "Step-by-step deployment: where the shared data source goes, how " +
        "to upload, why you should <b>not</b> click Refresh Fields, and " +
        "the download buttons. Set the <b>shared data source path</b> and " +
        "<b>report server URL</b> in the sidebar and they are baked into " +
        "every file you download.");

      await next(clickTab("extras"), "Extras — fidelity report & AI prompts",
        "The full fidelity breakdown (what mapped, what needs attention), " +
        "an audit trail of conversion decisions, and ready-made AI prompts " +
        "if you want a second opinion on any query or expression from " +
        "your own AI tooling.");

      await next(clickTab("burst"), "Bursting — one report, many recipients",
        "For reports that go out as per-recipient letters/invoices: this " +
        "tab generates the per-recipient pack and shows how to wire " +
        "email distribution through SSRS subscriptions with your service " +
        "account. This is the SSRS-native equivalent of Oracle's " +
        "<i>distribute=YES</i>.");

      await next(clickTab("subreports"), "Sub-reports — linked child reports",
        "When a report links to child reports (envelopes, detail pages), " +
        "they're detected automatically and listed here. Drop the child's " +
        "XML and it converts through the same pipeline — drill-through " +
        "links in the parent then point at the child on your server.");

      await next($("#adv-views-toggle") || $(".tab[data-tab='mockup']"),
        "That's the whole flow",
        "<b>Drop → read the verdict → preview → download → deploy.</b> " +
        "Advanced views stay hidden until you need them (the toggle above " +
        "the tabs). Verdicts are honest: if this tool says READY, it has " +
        "checked the report compiles, binds and renders — and if it says " +
        "RED, it tells you exactly why.");
    } catch (e) {
      if (!e || !e.tourEnded) throw e;
    } finally {
      cleanup();
      clickTab("mockup");
      if (typeof window.toast === "function") {
        window.toast("Tour ended — the app is yours.", "ok");
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
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose for the empty-state hero's inline button, if present.
  window.o2sRunTour = runTour;
})();
