const state = { mastery: "", sort: "recent", q: "", list: [] };

const els = {
  rows: document.getElementById("vocab-rows"),
  masterySeg: document.getElementById("mastery-segment"),
  sortSel: document.getElementById("sort-select"),
  search: document.getElementById("search-input"),
  count: document.getElementById("count-label"),
  toast: document.getElementById("toast"),
};

const MASTERY = ["new", "learning", "familiar", "mastered"];
const MASTERY_CN = ["新词", "学习中", "熟悉", "掌握"];

function toast(msg, err = false) {
  els.toast.textContent = msg;
  els.toast.classList.toggle("error", err);
  els.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => els.toast.classList.remove("show"), 2000);
}

function escapeHtml(s = "") {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderRow(v) {
  const contextBlock = v.context
    ? `<div class="vocab-context">
         "${escapeHtml((v.context || "").slice(0, 400))}"
         ${v.article_title ? `<span class="source">— ${escapeHtml(v.article_title)}</span>` : ""}
       </div>`
    : v.note
    ? `<div class="vocab-context">${escapeHtml(v.note)}</div>`
    : `<div class="vocab-context" style="color:var(--ink-mute);font-style:normal;">—</div>`;

  const masteryBtns = MASTERY_CN.map(
    (label, i) => `
    <button class="${v.mastery === i ? "active" : ""}" data-mastery="${i}" title="${MASTERY[i]}">
      ${label}
    </button>`
  ).join("");

  return `
    <div class="vocab-row" data-vid="${v.id}">
      <div class="vocab-word">
        ${escapeHtml(v.word)}
        <span class="lemma">${escapeHtml(v.lemma)}</span>
      </div>
      ${contextBlock}
      <div class="vocab-mastery">${masteryBtns}</div>
      <div class="vocab-actions">
        ${v.article_id ? `<a class="btn-chip" href="/articles/${v.article_id}">来源</a>` : ""}
        <button class="btn-chip" data-action="edit">编辑</button>
        <button class="btn-chip" data-action="delete">✕</button>
      </div>
    </div>`;
}

function render() {
  els.rows.innerHTML = state.list.length
    ? state.list.map(renderRow).join("")
    : `<div class="empty-state"><h2>还没有收录的生词</h2><p>去文章里划词加入吧。</p></div>`;
  els.count.textContent = `${state.list.length} 个`;
  wire();
}

function wire() {
  els.rows.querySelectorAll(".vocab-row").forEach((row) => {
    const vid = Number(row.dataset.vid);
    row.querySelectorAll(".vocab-mastery button").forEach((b) => {
      b.addEventListener("click", async () => {
        const m = Number(b.dataset.mastery);
        await fetch(`/api/vocabulary/${vid}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mastery: m }),
        });
        const it = state.list.find((v) => v.id === vid);
        if (it) it.mastery = m;
        render();
      });
    });
    row.querySelectorAll('[data-action="delete"]').forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("删除这个生词？")) return;
        await fetch(`/api/vocabulary/${vid}`, { method: "DELETE" });
        state.list = state.list.filter((v) => v.id !== vid);
        render();
        toast("已删除");
      })
    );
    row.querySelectorAll('[data-action="edit"]').forEach((b) =>
      b.addEventListener("click", async () => {
        const it = state.list.find((v) => v.id === vid);
        const note = prompt("更新释义/记忆点：", it?.note || "");
        if (note === null) return;
        const resp = await fetch(`/api/vocabulary/${vid}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        });
        const updated = await resp.json();
        const idx = state.list.findIndex((v) => v.id === vid);
        if (idx >= 0) state.list[idx] = { ...state.list[idx], ...updated };
        render();
        toast("已更新");
      })
    );
  });
}

async function load() {
  const p = new URLSearchParams({ sort: state.sort });
  if (state.mastery !== "") p.set("mastery", state.mastery);
  if (state.q) p.set("q", state.q);
  const resp = await fetch(`/api/vocabulary?${p}`);
  const data = await resp.json();
  state.list = data.words || [];
  render();
}

els.masterySeg.querySelectorAll("button").forEach((b) =>
  b.addEventListener("click", () => {
    els.masterySeg.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.mastery = b.dataset.mastery;
    load();
  })
);
els.sortSel.addEventListener("change", (e) => {
  state.sort = e.target.value;
  load();
});
let t;
els.search.addEventListener("input", (e) => {
  clearTimeout(t);
  t = setTimeout(() => {
    state.q = e.target.value.trim();
    load();
  }, 250);
});

load();
