/* =====================================================================
   scroll_regions.js — the wide things scroll, and the keyboard can reach
   them.

   The reflow contract (see the REFLOW LAYER in style.css) says the page
   never scrolls sideways and wide content scrolls inside its own box.
   That is only half a solution: in Chrome and Safari a <pre> that scrolls
   is NOT in the tab order, so at 320px or at 200% zoom a keyboard user can
   see a scrollbar under a line of SQL and has no way to move it. WCAG
   2.1.1 Keyboard.

   So: any box in the views that actually overflows sideways becomes a tab
   stop with a name that says what it holds. The ring comes from the one
   focus-ring rule in style.css ([tabindex]:focus-visible), and the marker
   attribute data-scrollable="x" is what the stylesheet and
   tests/test_reflow_small_viewports.py both key off.

   The marking is re-run whenever it could have changed — a new conversion,
   a tab switch, a window resize, a zoom step — and is REMOVED again when a
   box stops overflowing, because a tab stop that does nothing is noise.
   ===================================================================== */
(function () {
  "use strict";

  var ROOTS = [".tab-panels", ".howto-panel"];
  var MARK = "data-scrollable";

  function scrollsSideways(el) {
    var cs;
    try { cs = getComputedStyle(el); } catch (e) { return false; }
    var ox = cs.overflowX;
    if (ox !== "auto" && ox !== "scroll") return false;
    // 2px of slack: sub-pixel layout rounding is not a scrollbar.
    return el.scrollWidth > el.clientWidth + 2;
  }

  /* The name a screen reader reads when focus lands in the box. Prefer the
     heading the box sits under — "RDL XML", "Q_INV" — so the user is told
     WHICH listing they are in, not merely that something scrolls. */
  function nameFor(el) {
    var heading = null;
    var card = el.closest(".panel-card, .query-card, .burst-section, .deploy-step, .howto-section, .extras-card");
    if (card) heading = card.querySelector("h1,h2,h3,h4,.panel-title,.query-name,.sxs-head");
    if (!heading) {
      var prev = el.previousElementSibling;
      while (prev && !heading) {
        if (/^H[1-6]$/.test(prev.tagName)) heading = prev;
        prev = prev.previousElementSibling;
      }
    }
    // Nothing nearby names it (the simulated report sheet, for one) — then
    // the view it lives in does: "Preview", "Extras".
    if (!heading) {
      var panel = el.closest(".tab-panel");
      var by = panel && panel.getAttribute("aria-labelledby");
      if (by) heading = document.getElementById(by);
    }
    var label = heading ? (heading.textContent || "").replace(/\s+/g, " ").trim() : "";
    label = label.replace(/\s*\d+$/, "").trim();     // drop a tab's count badge
    if (label.length > 60) label = label.slice(0, 60).trim();
    return (label ? label + " — " : "") +
           "wide content, scrolls sideways. Use the left and right arrow keys.";
  }

  function mark(el) {
    if (el.getAttribute(MARK) === "x") return;
    el.setAttribute(MARK, "x");
    if (!el.hasAttribute("tabindex")) {
      el.setAttribute("tabindex", "0");
      el.setAttribute("data-scroll-tabindex", "own");
    }
    // role=group only on boxes that MEAN nothing already. Putting it on a
    // <table> would overwrite the table role and take the rows, columns and
    // header associations away from a screen-reader user — a far worse loss
    // than the one this file is fixing. A labelled <table> keeps its own
    // role and is named by the aria-label below.
    if (!el.getAttribute("role") && /^(PRE|DIV|SPAN|P)$/.test(el.tagName)) {
      el.setAttribute("role", "group");
      el.setAttribute("data-scroll-role", "own");
    }
    if (!el.getAttribute("aria-label") && !el.getAttribute("aria-labelledby")) {
      el.setAttribute("aria-label", nameFor(el));
      el.setAttribute("data-scroll-label", "own");
    }
  }

  function unmark(el) {
    if (el.getAttribute(MARK) !== "x") return;
    // Never pull the tab stop out from under the element that has focus.
    if (document.activeElement === el) return;
    el.removeAttribute(MARK);
    if (el.getAttribute("data-scroll-tabindex") === "own") {
      el.removeAttribute("tabindex"); el.removeAttribute("data-scroll-tabindex");
    }
    if (el.getAttribute("data-scroll-role") === "own") {
      el.removeAttribute("role"); el.removeAttribute("data-scroll-role");
    }
    if (el.getAttribute("data-scroll-label") === "own") {
      el.removeAttribute("aria-label"); el.removeAttribute("data-scroll-label");
    }
  }

  function sweep() {
    try {
      ROOTS.forEach(function (sel) {
        var root = document.querySelector(sel);
        if (!root) return;
        // A curated list, not every node: the HTML mockup alone can be
        // thousands of absolutely-placed boxes, and getComputedStyle on all
        // of them on every mutation would cost more than it is worth.
        // [style*="overflow-x"] catches the scrollers the mockup generator
        // writes inline (the .o2s-desk the simulated page sits on), so a
        // backend change cannot quietly drop one out of the tab order.
        var boxes = root.querySelectorAll(
          "pre,table,.sxs-scroll,.mockup-host,.render-host,.extras-table-wrap," +
          ".code-block,.o2s-desk,[style*='overflow-x'],[" + MARK + "]");
        Array.prototype.forEach.call(boxes, function (el) {
          if (scrollsSideways(el)) mark(el); else unmark(el);
        });
      });
    } catch (e) {
      // Never let a decoration break the app.
      if (window.console) console.warn("[Oracle2SSRS] scroll-region sweep failed:", e);
    }
  }

  /* The arrow keys have to MOVE the box.

     Measured in the shipped browser: with focus on a scrolling <pre>,
     ArrowRight scrolled the DOCUMENT and left the box where it was — the
     engine picks the viewport, not the focused scroller. A tab stop that
     cannot be moved is not keyboard access, so the keys are handled here.
     Only when the box itself is the event target: a control inside it
     keeps its own arrow-key behaviour. */
  var STEP = 48;
  function onKey(e) {
    var el = e.target;
    if (!el || !el.getAttribute || el.getAttribute(MARK) !== "x") return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    var max = el.scrollWidth - el.clientWidth, to = null;
    if (e.key === "ArrowRight") to = Math.min(max, el.scrollLeft + STEP);
    else if (e.key === "ArrowLeft") to = Math.max(0, el.scrollLeft - STEP);
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = max;
    else return;
    el.scrollLeft = to;
    e.preventDefault();          // otherwise the page scrolls instead
  }

  var pending = null;
  function schedule() {
    if (pending) return;
    pending = setTimeout(function () { pending = null; sweep(); }, 120);
  }
  window.o2sMarkScrollRegions = sweep;   // callable from tests and from app code

  function start() {
    sweep();
    // A conversion replaces whole panels; a tab switch reveals boxes that
    // measured zero while hidden; a zoom step or a window resize changes
    // whether anything overflows at all.
    try {
      var mo = new MutationObserver(schedule);
      ROOTS.forEach(function (sel) {
        var root = document.querySelector(sel);
        if (root) mo.observe(root, { childList: true, subtree: true });
      });
    } catch (e) { /* no MutationObserver: the listeners below still fire */ }
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", schedule);
    document.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest(".tab,.tabbar,details,summary,.mockup-mode-toggle"))
        schedule();
    }, true);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start);
  else start();
})();
