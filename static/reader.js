/* eslint-disable no-console */
// Reader page: selection toolbar → highlights, notes, vocabulary.
// Annotations are anchored by XPath + offset relative to #reader-body.
// Fallback: locate `quote` within plain text using prefix/suffix context.

const ARTICLE_ID = Number(document.getElementById("reader-main").dataset.articleId);
const root = document.getElementById("reader-body");

const els = {
  root,
  toolbar: document.getElementById("anno-toolbar"),
  annoList: document.getElementById("anno-list"),
  vocabList: document.getElementById("vocab-list"),
  annoCount: document.getElementById("anno-count"),
  vocabCount: document.getElementById("vocab-count"),
  toast: document.getElementById("toast"),
  actions: document.getElementById("reader-actions"),
  noteModal: document.getElementById("modal-note"),
  vocabModal: document.getElementById("modal-vocab"),
};

const state = {
  annotations: [],
  vocabulary: [],
  currentRange: null, // last selected range (cloned)
  currentRangeMeta: null, // serialized anchor
};

// ---------- tiny utils ----------
function toast(msg, isErr = false) {
  els.toast.textContent = msg;
  els.toast.classList.toggle("error", isErr);
  els.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => els.toast.classList.remove("show"), 2400);
}

function escapeHtml(s = "") {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ---------- XPath serialization ----------
function nodeIndexAmongSameName(node) {
  let i = 1;
  let sib = node.previousSibling;
  while (sib) {
    if (sib.nodeType === node.nodeType && sib.nodeName === node.nodeName) i += 1;
    sib = sib.previousSibling;
  }
  return i;
}

function nodeToXPath(node, rootEl) {
  if (!node) return "";
  const parts = [];
  let cur = node;
  while (cur && cur !== rootEl) {
    const parent = cur.parentNode;
    if (!parent) break;
    if (cur.nodeType === Node.TEXT_NODE) {
      // index among text() siblings
      let ti = 1;
      let s = cur.previousSibling;
      while (s) {
        if (s.nodeType === Node.TEXT_NODE) ti += 1;
        s = s.previousSibling;
      }
      parts.unshift(`text()[${ti}]`);
    } else if (cur.nodeType === Node.ELEMENT_NODE) {
      const idx = nodeIndexAmongSameName(cur);
      parts.unshift(`${cur.nodeName.toLowerCase()}[${idx}]`);
    }
    cur = parent;
  }
  return parts.join("/");
}

function xpathToNode(xpath, rootEl) {
  if (!xpath) return null;
  const segs = xpath.split("/").filter(Boolean);
  let cur = rootEl;
  for (const seg of segs) {
    const m = seg.match(/^([a-z0-9()]+)\[(\d+)\]$/i);
    if (!m) return null;
    const name = m[1];
    const idx = Number(m[2]);
    if (name === "text()") {
      let found = null;
      let ti = 0;
      for (const child of cur.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          ti += 1;
          if (ti === idx) { found = child; break; }
        }
      }
      if (!found) return null;
      cur = found;
    } else {
      let found = null;
      let ci = 0;
      for (const child of cur.childNodes) {
        if (
          child.nodeType === Node.ELEMENT_NODE &&
          child.nodeName.toLowerCase() === name
        ) {
          ci += 1;
          if (ci === idx) { found = child; break; }
        }
      }
      if (!found) return null;
      cur = found;
    }
  }
  return cur;
}

function serializeRange(range, rootEl) {
  const quote = range.toString();
  // compute prefix/suffix context (up to 32 chars) using the plain text walk
  const { prefix, suffix } = contextFromRange(range, rootEl, 32);
  return {
    start_xpath: nodeToXPath(range.startContainer, rootEl),
    start_offset: range.startOffset,
    end_xpath: nodeToXPath(range.endContainer, rootEl),
    end_offset: range.endOffset,
    quote,
    prefix,
    suffix,
  };
}

function contextFromRange(range, rootEl, window = 32) {
  // Build plain text with positions; find the range by walking to startContainer/offset.
  const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT);
  let acc = "";
  let startPos = -1;
  let endPos = -1;
  let node;
  while ((node = walker.nextNode())) {
    const isStart = node === range.startContainer;
    const isEnd = node === range.endContainer;
    if (isStart) startPos = acc.length + range.startOffset;
    if (isEnd) endPos = acc.length + range.endOffset;
    acc += node.nodeValue;
  }
  if (startPos < 0 || endPos < 0) return { prefix: "", suffix: "" };
  return {
    prefix: acc.slice(Math.max(0, startPos - window), startPos),
    suffix: acc.slice(endPos, endPos + window),
  };
}

function deserializeRange(anno, rootEl) {
  // 1) try direct xpath + offset
  try {
    const startNode = xpathToNode(anno.start_xpath, rootEl);
    const endNode = xpathToNode(anno.end_xpath, rootEl);
    if (startNode && endNode) {
      const r = document.createRange();
      r.setStart(startNode, Math.min(anno.start_offset, (startNode.nodeValue || "").length));
      r.setEnd(endNode, Math.min(anno.end_offset, (endNode.nodeValue || "").length));
      if (r.toString() && r.toString() === anno.quote) return r;
      // sometimes whitespace differs — still accept if close
      if (r.toString().replace(/\s+/g, " ").trim() === (anno.quote || "").replace(/\s+/g, " ").trim()) {
        return r;
      }
    }
  } catch (e) {
    // fall through
  }
  // 2) fallback: locate via plain text + prefix/suffix
  return findRangeByText(anno.quote, anno.prefix, anno.suffix, rootEl);
}

function findRangeByText(quote, prefix, suffix, rootEl) {
  if (!quote) return null;
  // Flatten text, keep mapping back to nodes.
  const map = []; // array of {node, start}
  const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT);
  let full = "";
  let node;
  while ((node = walker.nextNode())) {
    map.push({ node, start: full.length, length: node.nodeValue.length });
    full += node.nodeValue;
  }
  const needle = quote;
  const ctx = (prefix || "") + needle + (suffix || "");
  let idx = prefix ? full.indexOf(ctx) : -1;
  let foundStart = -1;
  if (idx >= 0) {
    foundStart = idx + prefix.length;
  } else {
    // try each occurrence of quote; pick the one whose context matches best
    let scan = 0;
    let best = -1;
    let bestScore = -1;
    while ((scan = full.indexOf(needle, scan)) !== -1) {
      const p = full.slice(Math.max(0, scan - 32), scan);
      const s = full.slice(scan + needle.length, scan + needle.length + 32);
      const score = similar(p, prefix) + similar(s, suffix);
      if (score > bestScore) { bestScore = score; best = scan; }
      scan += 1;
    }
    foundStart = best;
  }
  if (foundStart < 0) return null;
  const foundEnd = foundStart + needle.length;
  const startInfo = posToNode(map, foundStart);
  const endInfo = posToNode(map, foundEnd);
  if (!startInfo || !endInfo) return null;
  const r = document.createRange();
  r.setStart(startInfo.node, startInfo.offset);
  r.setEnd(endInfo.node, endInfo.offset);
  return r;
}

function posToNode(map, pos) {
  for (const m of map) {
    if (pos >= m.start && pos <= m.start + m.length) {
      return { node: m.node, offset: pos - m.start };
    }
  }
  return null;
}

function similar(a = "", b = "") {
  // cheap: count matching suffix/prefix characters
  const n = Math.min(a.length, b.length);
  let score = 0;
  for (let i = 1; i <= n; i++) {
    if (a.slice(-i) === b.slice(-i)) score = i; else break;
  }
  return score;
}

// ---------- highlight rendering ----------
function wrapRangeAsMark(range, { annoId, color = "yellow", hasNote = false, title = "" }) {
  if (!range || range.collapsed) return;
  // Collect all text nodes that intersect the range.
  const textNodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      if (!range.intersectsNode(n)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let n;
  while ((n = walker.nextNode())) textNodes.push(n);

  for (const tn of textNodes) {
    const isStart = tn === range.startContainer;
    const isEnd = tn === range.endContainer;
    const from = isStart ? range.startOffset : 0;
    const to = isEnd ? range.endOffset : tn.nodeValue.length;
    if (to <= from) continue;
    // split text node so we can wrap [from,to]
    let mid = tn;
    if (from > 0) mid = mid.splitText(from);
    if (to - from < mid.nodeValue.length) mid.splitText(to - from);
    const mark = document.createElement("mark");
    mark.className = "anno";
    mark.dataset.annoId = String(annoId);
    mark.dataset.color = color;
    if (hasNote) mark.classList.add("has-note");
    if (title) mark.title = title;
    mid.parentNode.insertBefore(mark, mid);
    mark.appendChild(mid);
  }
}

function clearHighlights() {
  for (const m of [...root.querySelectorAll("mark.anno")]) {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    parent.removeChild(m);
    parent.normalize();
  }
}

function reapplyHighlights() {
  clearHighlights();
  const sorted = [...state.annotations].sort((a, b) => {
    // apply shorter quotes last so they land on top
    return (b.quote || "").length - (a.quote || "").length;
  });
  for (const anno of sorted) {
    const r = deserializeRange(anno, root);
    if (!r) continue;
    try {
      wrapRangeAsMark(r, {
        annoId: anno.id,
        color: anno.color || "yellow",
        hasNote: anno.kind === "note" || !!anno.comment,
        title: anno.comment || "",
      });
    } catch (e) {
      console.warn("apply highlight failed", e);
    }
  }
}

// ---------- side panels ----------
function renderAnnoList() {
  const list = state.annotations;
  els.annoCount.textContent = list.length ? `${list.length}` : "";
  if (!list.length) {
    els.annoList.innerHTML = '<div class="panel-empty">选中正文任意文本，即可高亮或添加批注。</div>';
    return;
  }
  els.annoList.innerHTML = list
    .map(
      (a) => `
      <div class="panel-item" data-anno-id="${a.id}">
        <div class="quote">"${escapeHtml(a.quote)}"</div>
        ${a.comment ? `<div class="note">${escapeHtml(a.comment)}</div>` : ""}
        <div class="meta">
          <span>${a.kind === "note" ? "批注" : "高亮"} · ${a.color || ""}</span>
          <button data-act="delete">删除</button>
        </div>
      </div>`
    )
    .join("");
  els.annoList.querySelectorAll(".panel-item").forEach((el) => {
    const id = el.dataset.annoId;
    el.addEventListener("click", (e) => {
      if (e.target.dataset.act === "delete") {
        deleteAnnotation(id);
      } else {
        scrollToAnno(id);
      }
    });
  });
}

function renderVocabList() {
  const list = state.vocabulary;
  els.vocabCount.textContent = list.length ? `${list.length}` : "";
  if (!list.length) {
    els.vocabList.innerHTML = '<div class="panel-empty">把陌生词组"+ Vocabulary"存入生词本。</div>';
    return;
  }
  els.vocabList.innerHTML = list
    .map(
      (v) => `
      <div class="panel-item" data-vocab-id="${v.id}">
        <div class="lemma">${escapeHtml(v.word)}</div>
        <div class="note" style="color:var(--ink-mute);">${escapeHtml(v.lemma)}</div>
        ${v.note ? `<div class="note">${escapeHtml(v.note)}</div>` : ""}
        <div class="meta">
          <span>${["new","learning","familiar","mastered"][v.mastery] || "new"}</span>
          <button data-act="delete">移除</button>
        </div>
      </div>`
    )
    .join("");
  els.vocabList.querySelectorAll(".panel-item").forEach((el) => {
    const id = el.dataset.vocabId;
    el.addEventListener("click", (e) => {
      if (e.target.dataset.act === "delete") deleteVocab(id);
    });
  });
}

function scrollToAnno(id) {
  const mark = root.querySelector(`mark.anno[data-anno-id="${id}"]`);
  if (mark) {
    mark.scrollIntoView({ behavior: "smooth", block: "center" });
    mark.animate(
      [{ background: "#ffa" }, { background: "#fff3a8" }],
      { duration: 1200 }
    );
  }
}

// ---------- selection toolbar ----------
function positionToolbar(range) {
  const rect = range.getBoundingClientRect();
  if (!rect || (rect.width === 0 && rect.height === 0)) return;
  const top = window.scrollY + rect.top - 44;
  const left = window.scrollX + rect.left + rect.width / 2;
  els.toolbar.style.top = `${top}px`;
  els.toolbar.style.left = `${left}px`;
  els.toolbar.classList.add("show");
}

function hideToolbar() {
  els.toolbar.classList.remove("show");
}

document.addEventListener("selectionchange", () => {
  const sel = document.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
    // Give a grace moment so clicks on toolbar itself still work.
    setTimeout(() => {
      if (document.getSelection().isCollapsed) hideToolbar();
    }, 50);
    return;
  }
  const range = sel.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) {
    hideToolbar();
    return;
  }
  if (!range.toString().trim()) {
    hideToolbar();
    return;
  }
  state.currentRange = range.cloneRange();
  state.currentRangeMeta = serializeRange(range, root);
  positionToolbar(range);
});

els.toolbar.addEventListener("mousedown", (e) => e.preventDefault()); // keep selection
els.toolbar.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.classList.contains("swatch")) {
    const color = btn.dataset.color;
    saveHighlight({ color });
  } else if (btn.dataset.action === "note") {
    openNoteModal();
  } else if (btn.dataset.action === "vocab") {
    openVocabModal();
  }
});

// ---------- API calls ----------
async function fetchAnnotations() {
  const resp = await fetch(`/api/articles/${ARTICLE_ID}/annotations`);
  const data = await resp.json();
  state.annotations = data.annotations || [];
  reapplyHighlights();
  renderAnnoList();
}

async function fetchVocabForArticle() {
  const resp = await fetch(`/api/vocabulary`);
  const data = await resp.json();
  state.vocabulary = (data.words || []).filter((v) => v.article_id === ARTICLE_ID);
  renderVocabList();
}

async function saveHighlight({ color = "yellow", kind = "highlight", comment = null }) {
  if (!state.currentRangeMeta || !state.currentRangeMeta.quote) {
    toast("请先选中一段文本", true);
    return null;
  }
  const body = {
    ...state.currentRangeMeta,
    kind,
    color,
    comment,
  };
  try {
    const resp = await fetch(`/api/articles/${ARTICLE_ID}/annotations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(resp.statusText);
    const anno = await resp.json();
    state.annotations.push(anno);
    reapplyHighlights();
    renderAnnoList();
    hideToolbar();
    document.getSelection().removeAllRanges();
    toast(kind === "note" ? "批注已保存" : "已高亮");
    return anno;
  } catch (err) {
    toast("保存失败：" + err.message, true);
    return null;
  }
}

async function deleteAnnotation(id) {
  await fetch(`/api/annotations/${id}`, { method: "DELETE" });
  state.annotations = state.annotations.filter((a) => String(a.id) !== String(id));
  reapplyHighlights();
  renderAnnoList();
  toast("已删除");
}

async function saveVocab({ word, note }) {
  if (!word) return;
  const body = {
    word,
    context: state.currentRangeMeta?.prefix
      ? `${state.currentRangeMeta.prefix}${state.currentRangeMeta.quote}${state.currentRangeMeta.suffix}`
      : state.currentRangeMeta?.quote,
    article_id: ARTICLE_ID,
    note,
  };
  try {
    const resp = await fetch(`/api/vocabulary`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(resp.statusText);
    const v = await resp.json();
    // de-dup
    state.vocabulary = state.vocabulary.filter((x) => x.id !== v.id);
    state.vocabulary.unshift(v);
    renderVocabList();
    hideToolbar();
    toast(`已加入生词本：${v.word}`);
  } catch (err) {
    toast("保存失败：" + err.message, true);
  }
}

async function deleteVocab(id) {
  await fetch(`/api/vocabulary/${id}`, { method: "DELETE" });
  state.vocabulary = state.vocabulary.filter((v) => String(v.id) !== String(id));
  renderVocabList();
  toast("已移除");
}

// ---------- modals ----------
function openNoteModal() {
  if (!state.currentRangeMeta?.quote) return;
  document.getElementById("note-quoted").textContent = state.currentRangeMeta.quote;
  document.getElementById("note-comment").value = "";
  els.noteModal.classList.add("show");
  setTimeout(() => document.getElementById("note-comment").focus(), 50);
}
function openVocabModal() {
  if (!state.currentRangeMeta?.quote) return;
  document.getElementById("vocab-quoted").textContent = state.currentRangeMeta.quote;
  document.getElementById("vocab-word").value = state.currentRangeMeta.quote.trim();
  document.getElementById("vocab-note").value = "";
  els.vocabModal.classList.add("show");
  setTimeout(() => document.getElementById("vocab-word").focus(), 50);
}

els.noteModal.addEventListener("click", (e) => {
  if (e.target === els.noteModal) els.noteModal.classList.remove("show");
  const act = e.target.dataset?.act;
  if (act === "cancel") els.noteModal.classList.remove("show");
  if (act === "save") {
    const comment = document.getElementById("note-comment").value.trim();
    if (!comment) return toast("请先写点什么", true);
    saveHighlight({ kind: "note", color: "yellow", comment });
    els.noteModal.classList.remove("show");
  }
});
els.vocabModal.addEventListener("click", (e) => {
  if (e.target === els.vocabModal) els.vocabModal.classList.remove("show");
  const act = e.target.dataset?.act;
  if (act === "cancel") els.vocabModal.classList.remove("show");
  if (act === "save") {
    const word = document.getElementById("vocab-word").value.trim();
    const note = document.getElementById("vocab-note").value.trim();
    if (!word) return toast("请填写单词或词组", true);
    saveVocab({ word, note });
    els.vocabModal.classList.remove("show");
  }
});

// ---------- article flag buttons ----------
els.actions.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  try {
    const resp = await fetch(`/api/articles/${ARTICLE_ID}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    btn.classList.toggle("is-on", !!data[{
      favorite: "is_favorite", library: "in_library", read: "is_read",
    }[action]]);
    // simple label update
    if (action === "favorite") btn.innerHTML = data.is_favorite ? "★ 已收藏" : "☆ 收藏";
    if (action === "library") btn.innerHTML = `📚 ${data.in_library ? "已加入" : "加入每日库"}`;
    if (action === "read") btn.innerHTML = `✓ ${data.is_read ? "已读" : "标记已读"}`;
  } catch (err) {
    toast("操作失败：" + err.message, true);
  }
});

// ---------- init ----------
fetchAnnotations().then(() => fetchVocabForArticle());

root.addEventListener("click", (e) => {
  const a = e.target.closest?.("a[href]");
  if (a) {
    const href = a.getAttribute("href") || "";
    const isExternal = /^https?:\/\//i.test(href);
    if (isExternal) {
      e.preventDefault();
      toast("当前处于离线阅读模式，外链已忽略");
      return;
    }
  }
  const m = e.target.closest?.("mark.anno");
  if (!m) return;
  const id = m.dataset.annoId;
  const anno = state.annotations.find((a) => String(a.id) === String(id));
  if (anno?.comment) toast(anno.comment);
});
