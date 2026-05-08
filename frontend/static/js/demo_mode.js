/* =========================================================
   Oracle2SSRS — Take-a-tour walkthrough (rebuild)
   Robust step transitions: each step verifies success before
   advancing. Step 2 was skipping because state was scoped
   inside app.js and unreachable. We now poll for *visible*
   DOM signals instead.
   ========================================================= */
(function () {
  "use strict";
  if (window.__o2sTourLoaded) return;
  window.__o2sTourLoaded = true;

  function $(s) { return document.querySelector(s); }
  function $$(s) { return Array.from(document.querySelectorAll(s)); }

  function ensureStyles() {
    if (document.getElementById("o2s-tour-style")) return;
    const s = document.createElement("style");
    s.id = "o2s-tour-style";
    s.textContent = `
      #o2s-demo-tour-btn {
        position: fixed; bottom: 22px; right: 22px;
        background: linear-gradient(135deg,#b87dff,#5fc7ff);
        color:#fff; border:0; padding:11px 18px; border-radius:24px;
        font-weight:700; font-size:13px; letter-spacing:0.04em;
        cursor:pointer; z-index:99999;
        box-shadow:0 6px 20px rgba(184,125,255,0.45);
        font-family:inherit;
      }
      #o2s-demo-tour-btn:hover { filter:brightness(1.1); transform:translateY(-1px); }
      .demo-highlight { box-shadow: 0 0 0 3px #ffd166, 0 0 18px rgba(255,209,102,0.6) !important; transition: box-shadow 200ms; position: relative; z-index: 9000; }
      .demo-tooltip {
        position: absolute; z-index: 99998;
        background: #050314; color: #fff;
        border: 1px solid #b87dff;
        padding: 12px 14px; border-radius: 10px;
        font-size: 12.5px; font-family: inherit;
        max-width: 280px; line-height: 1.45;
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      }
      .demo-tooltip .tt-title { font-weight:700; font-size:13px; margin-bottom:4px; color:#d8c7ff; }
      .demo-tooltip .tt-meta  { font-size:10.5px; color:#9aa0c2; margin-top:6px; letter-spacing:0.12em; text-transform:uppercase; }
      .demo-tooltip .tt-next  {
        margin-top:10px; padding:5px 12px; border-radius:6px; cursor:pointer;
        background:#b87dff; color:#fff; border:0; font-weight:700; font-size:11px;
        font-family: inherit;
      }
      .demo-tooltip .tt-next:hover { filter: brightness(1.1); }
    `;
    document.head.appendChild(s);
  }

  function makeButton() {
    if (document.getElementById("o2s-demo-tour-btn")) return;
    const btn = document.createElement("button");
    btn.id = "o2s-demo-tour-btn";
    btn.textContent = "Take a tour";
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.style.opacity = "0.5";
      runTour().finally(() => { btn.remove(); });
    });
    document.body.appendChild(btn);
  }

  // Wait until predicate() returns truthy or timeout. Returns true/false.
  function waitFor(predicate, timeoutMs = 8000, intervalMs = 100) {
    return new Promise(resolve => {
      const t0 = Date.now();
      function check() {
        try {
          if (predicate()) { resolve(true); return; }
        } catch (e) { /* ignore */ }
        if (Date.now() - t0 > timeoutMs) { resolve(false); return; }
        setTimeout(check, intervalMs);
      }
      check();
    });
  }

  function showTooltip(target, title, message, position) {
    const old = document.querySelector(".demo-tooltip");
    if (old) old.remove();

    const rect = target.getBoundingClientRect();
    const tip = document.createElement("div");
    tip.className = "demo-tooltip";
    tip.innerHTML =
      '<div class="tt-title">' + title + '</div>' +
      '<div>' + message + '</div>' +
      '<div class="tt-meta">click to continue</div>';

    document.body.appendChild(tip);

    const tipRect = tip.getBoundingClientRect();
    let top = window.scrollY + rect.bottom + 12;
    let left = window.scrollX + rect.left;
    // Keep tooltip in viewport
    if (left + tipRect.width > window.innerWidth - 16) {
      left = window.innerWidth - tipRect.width - 16;
    }
    if (top + tipRect.height > window.scrollY + window.innerHeight - 16) {
      top = window.scrollY + rect.top - tipRect.height - 12;
    }
    tip.style.top  = top  + "px";
    tip.style.left = left + "px";

    return new Promise(resolve => {
      tip.addEventListener("click", () => { tip.remove(); resolve(); });
      // safety timeout — auto-advance after 12s if user doesn't click
      setTimeout(() => { if (tip.parentNode) { tip.remove(); resolve(); } }, 12000);
    });
  }

  function highlight(node) {
    document.querySelectorAll(".demo-highlight").forEach(n => n.classList.remove("demo-highlight"));
    if (node) node.classList.add("demo-highlight");
  }

  async function runTour() {
    const dropZone = $("#drop-zone");
    if (!dropZone) { return; }

    // --- Step 1: introduce drop zone ---
    highlight(dropZone);
    dropZone.scrollIntoView({behavior: "smooth", block: "center"});
    await showTooltip(dropZone, "1. Drop your Oracle XML here",
      "This zone accepts a single XML file or a whole folder of artifacts. " +
      "We'll click a sample for you next so you can see the rest.");

    // --- Step 2: click a sample chip — robust version ---
    // We don't depend on a specific filename anymore; we click whatever
    // chip is actually present, and we wait for the conversion's visible
    // signal: #summary-section becoming visible AND the mockup being filled.
    const chips = $$("#samples-list .sample-chip");
    if (!chips.length) {
      console.warn("[Tour] no sample chips found — skipping step 2");
    } else {
      const chip = chips[0];
      highlight(chip);
      chip.scrollIntoView({behavior: "smooth", block: "center"});
      await showTooltip(chip, "2. Loading a sample for you",
        "Clicking <b>" + (chip.textContent.trim() || "this sample") + "</b> now. " +
        "The conversion takes about a second.");

      try { chip.click(); } catch (e) { console.warn("[Tour] chip click failed", e); }

      // Wait for ANY of these to become true (whichever is fastest):
      //   - #summary-section visible (sidebar populated)
      //   - #mockup-host has content
      //   - status pill says "Converted"
      const got = await waitFor(() => {
        const summary = $("#summary-section");
        const mockup  = $("#mockup-host");
        const pill    = $("#status-pill");
        const summaryVisible = summary && !summary.hidden;
        const mockupHas      = mockup && mockup.children.length > 0;
        const pillOk         = pill && /converted|ok/i.test(pill.textContent);
        return summaryVisible || mockupHas || pillOk;
      }, 10000, 150);

      if (!got) {
        console.warn("[Tour] conversion did not finish in 10s — proceeding anyway");
      }
    }

    // --- Step 3: HTML mockup ---
    const tabMockup = $('.tab[data-tab="mockup"]');
    if (tabMockup) {
      tabMockup.click();
      await new Promise(r => setTimeout(r, 200));
      highlight(tabMockup);
      await showTooltip(tabMockup, "3. The HTML preview",
        "This is what your converted SSRS report will look like. " +
        "If it's a permit, you see a license layout. If it's a letter, " +
        "you see a letter layout. The converter detects shape automatically.");
    }

    // --- Step 4: download CTA ---
    const cta = $("#cta-download-rdl") || $("#download-rdl");
    if (cta) {
      highlight(cta);
      cta.scrollIntoView({behavior: "smooth", block: "center"});
      await showTooltip(cta, "4. Get the .rdl",
        "Click here to download the SSRS-ready file. " +
        "Drop it into Report Builder, point at your DEQ database, deploy.");
    }

    // --- Step 5: bursting tab (NEW) ---
    const tabBurst = $('.tab[data-tab="burst"]');
    if (tabBurst) {
      tabBurst.click();
      await new Promise(r => setTimeout(r, 200));
      highlight(tabBurst);
      await showTooltip(tabBurst, "5. Bursting / Email distribution",
        "If your report goes out as letters to multiple recipients, this tab " +
        "tells you exactly how to wire it up via your service-account email.");
    }

    // --- Done ---
    highlight(null);
    document.querySelectorAll(".demo-tooltip").forEach(n => n.remove());
    if (typeof window.toast === "function") {
      window.toast("Tour complete — happy converting!", "ok");
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
})();
