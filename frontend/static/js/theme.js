/* =========================================================
   Oracle2SSRS — colour theme (system / light / dark).

   This file is loaded SYNCHRONOUSLY from <head>, above the page body, on
   purpose. The stored preference has to be on the <html> element before
   the browser paints anything, or the operator sees a white flash and then
   a dark page. A deferred script, or one at the end of <body>, cannot do
   that.

   The whole mechanism is two lines of DOM:
     data-theme      -> "light" | "dark", or absent for "follow the system"
     data-theme-pref -> what the operator actually chose ("system" included)

   The stylesheet does the rest. See the COLOUR LAYER comment at the top of
   style.css for why there are two dark blocks.
   ========================================================= */
(function () {
  "use strict";

  var KEY = "o2s-theme";
  var CHOICES = ["system", "light", "dark"];
  var DARK_QUERY = "(prefers-color-scheme: dark)";

  function readPref() {
    try {
      var v = window.localStorage.getItem(KEY);
      return CHOICES.indexOf(v) >= 0 ? v : "system";
    } catch (e) {
      // Storage can be switched off entirely (private window, group policy).
      // The app still works; the choice just does not survive a reload.
      return "system";
    }
  }

  function savePref(pref) {
    try { window.localStorage.setItem(KEY, pref); } catch (e) { /* see above */ }
  }

  function systemIsDark() {
    return !!(window.matchMedia && window.matchMedia(DARK_QUERY).matches);
  }

  function resolve(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return systemIsDark() ? "dark" : "light";
  }

  function apply(pref) {
    var root = document.documentElement;
    if (pref === "light" || pref === "dark") root.setAttribute("data-theme", pref);
    else root.removeAttribute("data-theme");
    root.setAttribute("data-theme-pref", pref);
    return resolve(pref);
  }

  // ---- runs at parse time, before the first paint -----------------------
  // If <body> does not exist yet, the browser cannot have painted any page
  // content yet either — which is the whole no-flash claim, measured rather
  // than asserted. Kept on the public object so a test can read it.
  var bootedBeforeBody = (document.body === null);
  apply(readPref());

  // ---- plain-language state, for the live region ------------------------
  function describe(pref) {
    var now = resolve(pref);
    if (pref === "system") {
      return "Theme: matching this computer. It is " + now + " right now.";
    }
    return "Theme: " + now + ". Saved on this computer for next time.";
  }

  var repaint = null;   // set by wire(), so o2sTheme.set can reuse it

  function wire() {
    if (repaint) { repaint(readPref(), false); return; }
    var group = document.getElementById("theme-toggle");
    if (!group) return;
    var radios = group.querySelectorAll("input[name='o2s-theme']");
    if (!radios.length) return;
    var note = document.getElementById("theme-state");

    function paint(pref, save) {
      apply(pref);
      for (var i = 0; i < radios.length; i++) {
        radios[i].checked = (radios[i].value === pref);
      }
      if (note) note.textContent = describe(pref);
      if (save) savePref(pref);
    }
    repaint = paint;

    for (var i = 0; i < radios.length; i++) {
      radios[i].addEventListener("change", function (ev) {
        if (ev.target.checked) paint(ev.target.value, true);
      });
    }

    // Follow the machine while the operator is on "system", and keep the
    // spoken state honest when the OS flips at sunset.
    var mq = window.matchMedia ? window.matchMedia(DARK_QUERY) : null;
    if (mq) {
      var onSystemChange = function () {
        if (readPref() === "system" && note) note.textContent = describe("system");
      };
      if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
      else if (mq.addListener) mq.addListener(onSystemChange);
    }

    // A second tab is the same operator. Keep the windows agreeing.
    window.addEventListener("storage", function (ev) {
      if (ev && ev.key === KEY) paint(readPref(), false);
    });

    paint(readPref(), false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }

  // Exposed so tests (and the console) can read the state without guessing.
  window.o2sTheme = {
    bootedBeforeBody: bootedBeforeBody,
    get: readPref,
    resolved: function () { return resolve(readPref()); },
    set: function (pref) {
      if (CHOICES.indexOf(pref) < 0) return false;
      savePref(pref);
      apply(pref);
      wire();
      return true;
    }
  };
})();
