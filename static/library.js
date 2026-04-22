const state = { filter: "all", category: "", q: "", list: [], categories: {} };

const els = {
  list: document.getElementById("list"),
  segment: document.getElementById("filter-segment"),
  categorySel: document.getElementById("filter-category"),
  query: document.getElementById("filter-query"),
  count: document.getElementById("filter-count"),
  toast: document.getElementById("toast"),
};

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

function renderCard(a) {
  const hasImg = !!a.cover_image;
  return `
    <article class="card" data-id="${a.id}">
      <a class="card-img ${hasImg ? "" : "no-img"}" href="/articles/${a.id}">
        ${hasImg ? `<img src="${escapeHtml(a.cover_image)}" alt="" loading="lazy" />` : `<span>T</span>`}
      </a>
      <div class="card-body">
        <h3 class="card-title"><a href="/articles/${a.id}">${escapeHtml(a.title)}</a></h3>
        <p class="card-desc">${escapeHtml(a.description || "")}</p>
        <div class="card-foot">
          <span class="card-author">${escapeHtml(a.author || "TIME")}</span>
          <span>${escapeHtml(a.fetch_date || "")} · ${a.word_count || 0}w</span>
        </div>
        <div class="card-actions" data-article-id="${a.id}">
          <button class="btn-chip chip-favorite ${a.is_favorite ? "is-on" : ""}" data-action="favorite">
            ${a.is_favorite ? "★" : "☆"} <span>${a.is_favorite ? "已收藏" : "收藏"}</span>
          </button>
          <button class="btn-chip chip-library ${a.in_library ? "is-on" : ""}" data-action="library">
            📚 <span>${a.in_library ? "已加入" : "加入库"}</span>
          </button>
          <button class="btn-chip chip-read ${a.is_read ? "is-on" : ""}" data-action="read">
            ✓ <span>${a.is_read ? "已读" : "标读"}</span>
          </button>
        </div>
      </div>
    </article>`;
}

function render() {
  const html = state.list.length
    ? state.list.map(renderCard).join("")
    : `<div class="empty-state" style="grid-column:1/-1;">
         <h2>没有匹配的文章</h2>
         <p>换个筛选条件试试，或去首页继续加入文章。</p>
       </div>`;
  els.list.innerHTML = html;
  els.count.textContent = `${state.list.length} 篇`;
  wireCardActions();
}

function wireCardActions() {
  els.list.querySelectorAll(".card-actions button").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const action = btn.dataset.action;
      const id = btn.closest(".card-actions").dataset.articleId;
      try {
        const resp = await fetch(`/api/articles/${id}/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        if (!resp.ok) throw new Error(resp.statusText);
        const data = await resp.json();
        const idx = state.list.findIndex((a) => a.id === data.id);
        if (idx >= 0) state.list[idx] = { ...state.list[idx], ...data };
        render();
        toast("已更新");
      } catch (err) {
        toast("操作失败：" + err.message, true);
      }
    });
  });
}

async function load() {
  const params = new URLSearchParams({
    filter: state.filter,
    category: state.category,
    q: state.q,
  });
  const resp = await fetch(`/api/library?${params.toString()}`);
  const data = await resp.json();
  state.list = data.articles || [];
  state.categories = data.categories || {};
  populateCategories();
  render();
}

function populateCategories() {
  if (els.categorySel.options.length > 1) return;
  for (const [key, v] of Object.entries(state.categories)) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = `${v.label} · ${v.label_cn}`;
    els.categorySel.appendChild(opt);
  }
}

els.segment.querySelectorAll("button").forEach((b) =>
  b.addEventListener("click", () => {
    els.segment.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.filter = b.dataset.filter;
    load();
  })
);
els.categorySel.addEventListener("change", (e) => {
  state.category = e.target.value;
  load();
});
let t;
els.query.addEventListener("input", (e) => {
  clearTimeout(t);
  t = setTimeout(() => {
    state.q = e.target.value.trim();
    load();
  }, 250);
});

load();
