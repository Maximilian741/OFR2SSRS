/* =========================================================================
   Oracle2SSRS — syntax highlighting, served from this machine.

   WHY THIS FILE EXISTS
   The page used to pull Prism (one CSS + three JS files) from a public
   CDN, and its fonts from another. The operator this tool
   is built for runs it on a locked-down agency workstation that may have
   NO INTERNET AT ALL. On that machine every one of those requests either
   fails outright — so code listings render as grey walls of text — or,
   worse, hangs until the proxy times out, which the operator experiences
   as "the app doesn't load". A migration tool that needs the internet to
   show you an RDL is a tool that cannot be used at work.

   Vendoring Prism itself was not possible offline (nothing to download
   from, and no local copy on the machine), so this is a small highlighter
   written for exactly the two languages the app actually highlights:

       language-xml  — the generated RDL, and every XML excerpt
       language-sql  — the generated queries and the bursting query

   Anything else is left as plain text, which is what it already was.

   API
   `highlightElement(el)` is the only entry point app.js uses. The global is
   also aliased to `Prism` so the existing call sites keep working, and so
   the `.token` class names the stylesheet paints stay the ones the rest of
   the world uses.

   COLOUR lives in style.css, in the token layer, like every other colour
   in this app (tests/test_theme_tokens.py enforces that). Nothing here
   emits a colour, and nothing here touches the network.

   SAFETY: every character of the source text is HTML-escaped before it
   goes anywhere near innerHTML. The only markup this file produces is its
   own <span class="token ..."> wrappers.
   ========================================================================= */
(function () {
  "use strict";

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // `inner` is ALREADY escaped by every caller below.
  function wrap(cls, inner) {
    return '<span class="token ' + cls + '">' + inner + "</span>";
  }

  function tok(cls, text) {
    return wrap(cls, esc(text));
  }

  /* ---------------------------------------------------------------------
     The scanner.

     One combined regex per language instead of "try each rule at every
     character": a 100 KB RDL is ~3 million character positions, and the
     per-position loop cost seconds. With a single alternation the engine
     walks the string once and tells us which branch won, which keeps a
     tab switch instant. Every rule must therefore use NON-CAPTURING
     groups — the capture indexes are how we know which rule matched.
     --------------------------------------------------------------------- */
  function build(rules) {
    return {
      re: new RegExp(rules.map(function (r) {
        return "(" + r[1] + ")";
      }).join("|"), "gi"),
      rules: rules
    };
  }

  function scan(code, grammar) {
    var re = grammar.re, rules = grammar.rules;
    var out = "", last = 0, m;
    re.lastIndex = 0;
    while ((m = re.exec(code)) !== null) {
      if (m[0] === "") { re.lastIndex++; continue; }   // never spin
      if (m.index > last) out += esc(code.slice(last, m.index));
      var rule = null;
      for (var i = 0; i < rules.length; i++) {
        if (m[i + 1] !== undefined) { rule = rules[i]; break; }
      }
      out += rule && rule[2] ? rule[2](m[0]) : tok(rule ? rule[0] : "punctuation", m[0]);
      last = m.index + m[0].length;
    }
    if (last < code.length) out += esc(code.slice(last));
    return out;
  }

  /* ---------------------------------------------------------------------
     XML / markup
     --------------------------------------------------------------------- */
  // Attributes inside one tag. Quoted values may legally contain '>' — an
  // RDL expression such as ="a > b" does — so the tag pattern below counts
  // quotes rather than stopping at the first '>'.
  var ATTR = build([
    ["attr-name",
     "[A-Za-z_:][\\w:.\\-]*\\s*=\\s*(?:\"[^\"]*\"|'[^']*'|[^\\s\"'>]+)",
     function (text) {
       var m = /^([A-Za-z_:][\w:.\-]*)(\s*=\s*)([\s\S]*)$/.exec(text);
       if (!m) { return tok("attr-name", text); }
       return tok("attr-name", m[1]) + tok("punctuation", m[2]) +
              tok("attr-value", m[3]);
     }],
    ["attr-name", "[A-Za-z_:][\\w:.\\-]*"]
  ]);

  function renderTag(text) {
    var m = /^(<\/?)([A-Za-z_][\w:.\-]*)([\s\S]*?)(\/?>)$/.exec(text);
    if (!m) { return tok("tag", text); }
    return tok("punctuation", m[1]) +
           tok("tag", m[2]) +
           (m[3] ? scan(m[3], ATTR) : "") +
           tok("punctuation", m[4]);
  }

  var MARKUP = build([
    ["comment", "<!--[\\s\\S]*?-->"],
    ["cdata",   "<!\\[CDATA\\[[\\s\\S]*?\\]\\]>"],
    ["prolog",  "<\\?[\\s\\S]*?\\?>"],
    ["doctype", "<!DOCTYPE[\\s\\S]*?>"],
    ["tag",     "</?[A-Za-z_][\\w:.\\-]*(?:\\s+(?:[^<>\"']|\"[^\"]*\"|'[^']*')*)?/?>",
                renderTag],
    ["entity",  "&#?[0-9A-Za-z]+;"]
  ]);

  /* ---------------------------------------------------------------------
     SQL — Oracle on the way in, T-SQL on the way out, so the word list
     covers both dialects. Anything unrecognised stays plain text; a word
     this list misses loses its colour, it never loses its characters.
     --------------------------------------------------------------------- */
  var KEYWORDS = [
    "select", "from", "where", "and", "or", "not", "is", "in", "like",
    "between", "order", "by", "group", "having", "join", "inner", "left",
    "right", "full", "outer", "cross", "on", "as", "union", "all",
    "distinct", "into", "insert", "update", "delete", "set", "values",
    "case", "when", "then", "else", "end", "with", "exists", "asc", "desc",
    "top", "limit", "offset", "fetch", "next", "rows", "only", "create",
    "table", "view", "procedure", "function", "return", "returns",
    "declare", "begin", "if", "loop", "for", "while", "connect", "start",
    "prior", "siblings", "partition", "over", "using", "merge", "minus",
    "intersect", "any", "some", "cast", "pivot", "unpivot", "escape",
    "nulls", "first", "last", "level", "rownum", "sysdate", "dual",
    "unique", "primary", "key", "foreign", "references", "constraint",
    "index", "alter", "drop", "add", "column", "default", "check",
    "commit", "rollback", "grant", "revoke", "exec", "execute", "go"
  ];

  var SQL = build([
    ["comment",     "--[^\\n\\r]*"],
    ["comment",     "/\\*[\\s\\S]*?\\*/"],
    ["string",      "'(?:''|[^'])*'"],
    ["string",      "\"(?:\"\"|[^\"])*\""],
    ["variable",    "[:@][A-Za-z_]\\w*"],
    ["boolean",     "\\b(?:true|false|null)\\b"],
    ["keyword",     "\\b(?:" + KEYWORDS.join("|") + ")\\b"],
    ["function",    "\\b[A-Za-z_]\\w*(?=\\s*\\()"],
    ["number",      "\\b\\d+(?:\\.\\d+)?(?:[eE][+\\-]?\\d+)?\\b"],
    ["operator",    "<=|>=|<>|!=|\\|\\||[=<>+\\-*/%]"],
    ["punctuation", "[;(),.\\[\\]]"]
  ]);

  var GRAMMARS = {
    xml: MARKUP, markup: MARKUP, html: MARKUP, svg: MARKUP, rdl: MARKUP,
    sql: SQL
  };

  function languageOf(el) {
    var m = /\blanguage-([\w-]+)\b/.exec(el.className || "");
    return m ? m[1].toLowerCase() : "";
  }

  function highlight(code, language) {
    var grammar = GRAMMARS[String(language || "").toLowerCase()];
    return grammar ? scan(code, grammar) : esc(code);
  }

  function highlightElement(el) {
    if (!el) { return; }
    var grammar = GRAMMARS[languageOf(el)];
    if (!grammar) { return; }             // unknown language: leave it alone
    // textContent, not innerHTML: re-highlighting an already-highlighted
    // block must be a no-op, not a nest of spans inside spans.
    el.innerHTML = scan(el.textContent || "", grammar);
  }

  function highlightAll(root) {
    (root || document)
      .querySelectorAll('code[class*="language-"]')
      .forEach(highlightElement);
  }

  var api = {
    highlight: highlight,
    highlightElement: highlightElement,
    highlightAll: highlightAll,
    languages: Object.keys(GRAMMARS)
  };

  window.O2SHighlight = api;
  // The call sites, the stylesheet and the `.token` class names all speak
  // Prism. Keeping the global name means no other file has to change, and
  // a real Prism could be dropped back in without touching app.js.
  window.Prism = window.Prism || api;
})();
