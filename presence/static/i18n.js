/*
 * PRESENCE AI — page translation.
 *
 * The page follows the speech language picked in Settings: pick Arabic and
 * the whole page switches to Arabic (right-to-left), pick French and it
 * switches to French, and so on.
 *
 * How it works, in one paragraph: app.js keeps writing English, exactly as
 * before. This file watches the page and swaps every English text it knows
 * for the chosen language as it appears. Texts with numbers in them
 * ("You are rushing (152 wpm).") are matched by templates in which {} stands
 * for the changing part. Nothing the student says or types is translated:
 * anything inside [data-no-i18n] is left alone.
 *
 * Each language lives in its own file, i18n-<code>.js, loaded only when
 * that language is chosen.
 */
(function () {
  "use strict";

  const SUPPORTED = ["ar", "es", "fr", "de", "it", "pt", "nl", "pl", "el", "tr", "vi", "zh", "ja", "ko"];
  const RTL = ["ar"];
  const STORAGE_KEY = "presence-language";
  const ATTRS = ["placeholder", "aria-label", "title", "aria-description"];
  const SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1 };

  window.PRESENCE_I18N_DICTS = window.PRESENCE_I18N_DICTS || {};

  let lang = "en";
  let exact = new Map();
  let patterns = [];
  const missing = new Set();
  const nodeState = new WeakMap();   // text node -> { src, out }
  const attrState = new WeakMap();   // element -> { attr: { src, out } }
  let observer = null;
  let writing = false;

  function norm(s) { return String(s).replace(/\s+/g, " ").trim(); }

  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function compile(dict) {
    exact = new Map();
    patterns = [];
    Object.keys(dict || {}).forEach((key) => {
      const k = norm(key);
      const v = dict[key];
      if (!k || typeof v !== "string") return;
      if (!/\{n?\}/.test(k)) { exact.set(k, v); return; }
      // {} stands for any text, {n} for a number only.
      const parts = k.split(/(\{n?\})/);
      let src = "^";
      let weight = 0;
      parts.forEach((part) => {
        if (part === "{}") src += "([\\s\\S]*?)";
        else if (part === "{n}") { src += "(-?[\\d.,]+)"; weight += 0.5; }
        else { src += escapeRe(part); weight += part.length; }
      });
      patterns.push({ re: new RegExp(src + "$"), out: v, weight });
    });
    // Longest fixed text first, so the most specific template wins.
    patterns.sort((a, b) => b.weight - a.weight);
  }

  // How many of the source's English words are still in a translation.
  function englishLeft(s, src) {
    const words = new Set(String(src).match(/[A-Za-z]{3,}/g) || []);
    return (String(s).match(/[A-Za-z]{3,}/g) || []).filter((w) => words.has(w)).length;
  }

  function fill(out, caps) {
    // In a right-to-left page, numbers and Latin names inside a sentence are
    // isolated so the browser does not flip "2026-11-19" or "-30 dBFS".
    const iso = RTL.indexOf(lang) !== -1;
    return out.replace(/\{(\d+)\}/g, (_, i) => {
      const c = caps[+i];
      if (c === undefined || c === "") return "";
      return iso ? "\u2068" + c + "\u2069" : c;
    });
  }

  function lookup(k, depth, noSplit) {
    const hit = exact.get(k);
    if (hit !== undefined) return hit;
    // A template whose {} would swallow a whole extra sentence is only a
    // last resort: "Your average volume was {} dBFS, peaking at {}." must not
    // eat the two sentences glued on after it.
    let loose = null;
    for (const p of patterns) {
      const m = k.match(p.re);
      if (!m) continue;
      const raw = m.slice(1);
      if (raw.some((c) => /[.!?…]\s+\S/.test(c))) { if (!loose) loose = { p, raw }; continue; }
      const caps = raw.map((c) => (depth < 3 ? translate(c, depth + 1) : c));
      return fill(p.out, caps);
    }
    // Sentences glued together at run time: translate them in the longest
    // runs the dictionary knows ("Hello. Ask me…" is one entry of three
    // sentences, followed by a fourth that is an entry of its own).
    if (!noSplit) {
      const SEP = /(?<=[.!?…])\s+(?=\S)|\s+·\s+/g;
      const pieces = k.split(SEP);
      if (pieces.length > 1) {
        const seps = k.match(SEP) || [];
        const join = (a, b) => pieces.slice(a, b).map((x, i) => x + (a + i < b - 1 ? seps[a + i] : "")).join("");
        const out = [];
        let any = false;
        let i = 0;
        while (i < pieces.length) {
          let j = pieces.length;
          let t = null;
          for (; j > i; j--) {
            t = lookup(join(i, j), depth + 1, true);
            if (t !== null) break;
          }
          if (t === null) {
            if (/[A-Za-z]{2}/.test(pieces[i])) missing.add(pieces[i]);
            t = pieces[i];
            j = i + 1;
          } else {
            any = true;
          }
          let sep = j - 1 < seps.length ? seps[j - 1] : "";
          // No spaces between sentences in Chinese and Japanese.
          if ((lang === "zh" || lang === "ja") && /^\s+$/.test(sep) && /[。！？]$/.test(t)) sep = "";
          out.push(t + sep);
          i = j;
        }
        if (any) {
          const split = out.join("");
          if (!loose) return split;
          // Both readings work: keep the one that leaves less English behind.
          const viaTemplate = fill(loose.p.out, loose.raw.map((c) => (depth < 3 ? translate(c, depth + 1) : c)));
          return englishLeft(viaTemplate, k) < englishLeft(split, k) ? viaTemplate : split;
        }
      }
    }
    if (loose && !noSplit) {
      return fill(loose.p.out, loose.raw.map((c) => (depth < 3 ? translate(c, depth + 1) : c)));
    }
    // "0 fillers ·" is "0 fillers" with a separator left over from a list.
    const edge = k.match(/^([·•—–,;\s]*)([\s\S]*?)([\s·•—–,;]*)$/);
    if (edge && (edge[1] || edge[3]) && edge[2]) {
      const t = lookup(edge[2], depth + 1, noSplit);
      if (t !== null) return edge[1] + t + edge[3];
    }
    // "audible and steady." is "audible and steady" plus a full stop.
    const stop = k.match(/^(.*?[A-Za-z)])([.:!?])$/);
    if (stop) {
      const t = lookup(stop[1], depth + 1, noSplit);
      if (t !== null) {
        if (/[。．！？：]$/.test(t)) return t;
        // Chinese and Japanese use their own full stop and colon.
        if (lang === "zh" || lang === "ja") return t + ({ ".": "。", ":": "：", "!": "！", "?": "？" }[stop[2]] || stop[2]);
        return t + stop[2];
      }
    }
    return null;
  }

  /** Translate one English string. Unknown text comes back unchanged. */
  function translate(text, depth) {
    if (lang === "en" || text == null) return text;
    const s = String(text);
    const k = norm(s);
    if (!k || !/[A-Za-z]{2}/.test(k)) return s;
    const out = lookup(k, depth || 0);
    if (out === null) {
      if (!depth) missing.add(k);
      return s;
    }
    // Keep the spaces around the original, they are often layout.
    const lead = s.match(/^\s*/)[0];
    const trail = s.match(/\s*$/)[0];
    return lead + out + trail;
  }

  // Placeholders inside a no-translate area ("Listening…" in the transcript)
  // are page text, not the student's words, so they opt back in.
  const OPT_IN = ".transcript-empty, [data-i18n]";

  function skipped(el) {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (SKIP_TAGS[e.tagName]) return true;
      if (e.matches(OPT_IN)) return false;
      if (e.hasAttribute("data-no-i18n")) return true;
    }
    return false;
  }

  function doText(node) {
    const st = nodeState.get(node);
    let src = node.data;
    if (st && node.data === st.out) src = st.src;        // our own earlier write
    else if (st && node.data === st.src) src = st.src;
    if (!/[A-Za-z]/.test(src)) return;
    if (skipped(node.parentElement)) return;
    const out = lang === "en" ? src : translate(src);
    nodeState.set(node, { src, out });
    if (node.data !== out) node.data = out;
  }

  function doAttrs(el) {
    if (skipped(el)) return;
    let st = attrState.get(el);
    for (const a of ATTRS) {
      if (!el.hasAttribute(a)) continue;
      const cur = el.getAttribute(a);
      if (!st) { st = {}; attrState.set(el, st); }
      const prev = st[a];
      const src = prev && (cur === prev.out || cur === prev.src) ? prev.src : cur;
      const out = lang === "en" ? src : translate(src);
      st[a] = { src, out };
      if (cur !== out) el.setAttribute(a, out);
    }
  }

  // Elements marked data-i18n-html are translated as one piece, tags and
  // all, because "Let your voice<br>do the <em>talking.</em>" cut into three
  // fragments cannot be translated well.
  const htmlState = new WeakMap();
  function doHtml(el) {
    if (!htmlState.has(el)) htmlState.set(el, el.innerHTML);
    const src = htmlState.get(el);
    let out = src;
    if (lang !== "en") {
      const hit = exact.get(norm(src));
      if (hit !== undefined) out = hit;
      else missing.add(norm(src));
    }
    if (el.innerHTML !== out) el.innerHTML = out;
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) {
      if (SKIP_TAGS[root.tagName]) return;
      if (root.hasAttribute("data-no-i18n")) {
        root.querySelectorAll(OPT_IN).forEach(walk);
        return;
      }
      if (root.hasAttribute("data-i18n-html")) { doHtml(root); return; }
      doAttrs(root);
    }
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (n.nodeType === 1 && SKIP_TAGS[n.tagName]) return NodeFilter.FILTER_REJECT;
        if (n.nodeType === 1 && n.hasAttribute("data-no-i18n")) {
          n.querySelectorAll(OPT_IN).forEach(walk);
          return NodeFilter.FILTER_REJECT;
        }
        if (n.nodeType === 1 && n.hasAttribute("data-i18n-html")) {
          doHtml(n);
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let n = tw.nextNode();
    while (n) {
      if (n.nodeType === 3) doText(n); else doAttrs(n);
      n = tw.nextNode();
    }
  }

  function runQuietly(fn) {
    writing = true;
    try { fn(); } finally {
      if (observer) observer.takeRecords();
      writing = false;
    }
  }

  function onMutations(list) {
    if (writing) return;
    runQuietly(() => {
      for (const m of list) {
        if (m.type === "characterData") doText(m.target);
        else if (m.type === "attributes") doAttrs(m.target);
        else m.addedNodes.forEach(walk);
      }
    });
  }

  function startObserver() {
    if (observer || !document.body) return;
    observer = new MutationObserver(onMutations);
    observer.observe(document.body, {
      subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ATTRS,
    });
  }

  const titleSrc = { value: null };
  function applyDocument() {
    const root = document.documentElement;
    root.lang = lang;
    root.dir = RTL.indexOf(lang) !== -1 ? "rtl" : "ltr";
    if (titleSrc.value === null) titleSrc.value = document.title;
    document.title = lang === "en" ? titleSrc.value : translate(titleSrc.value);
  }

  function loadDict(code) {
    if (window.PRESENCE_I18N_DICTS[code]) return Promise.resolve(window.PRESENCE_I18N_DICTS[code]);
    return new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = "i18n-" + code + ".js";
      s.onload = () => resolve(window.PRESENCE_I18N_DICTS[code] || null);
      s.onerror = () => resolve(null);
      document.head.appendChild(s);
    });
  }

  /** "ar-SA" -> "ar". Anything unsupported becomes English. */
  function pageLanguageFor(code) {
    const short = String(code || "en").slice(0, 2).toLowerCase();
    return SUPPORTED.indexOf(short) !== -1 ? short : "en";
  }

  /** Switch the page to the language of a speech locale like "fr-FR". */
  async function setLanguage(code) {
    const next = pageLanguageFor(code);
    let dict = null;
    if (next !== "en") {
      dict = await loadDict(next);
      if (!dict) return lang;            // file missing: stay as we are
    }
    lang = next;
    compile(dict);
    missing.clear();
    runQuietly(() => { applyDocument(); walk(document.body); });
    startObserver();
    try { document.dispatchEvent(new CustomEvent("presence:language", { detail: { lang } })); } catch (e) { /* old browser */ }
    return lang;
  }

  function savedLanguage() {
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }

  function saveLanguage(code) {
    try { localStorage.setItem(STORAGE_KEY, code); } catch (e) { /* private mode */ }
  }

  window.PresenceI18n = {
    t: (s) => translate(s),
    setLanguage,
    savedLanguage,
    saveLanguage,
    pageLanguageFor,
    get lang() { return lang; },
    missing: () => Array.from(missing),
  };
})();
