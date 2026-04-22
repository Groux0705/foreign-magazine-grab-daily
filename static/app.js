// Today's feed page.
const state = {
  date: null,
  availableDates: [],
  categories: {},
  categoryOrder: [],
  articles: {}, // category -> [article]
};

const els = {
  main: document.getElementById("main"),
  dateSelect: document.getElementById("date-select"),
  metaDate: document.getElementById("meta-date"),
  metaFetched: document.getElementById("meta-fetched"),
  metaCount: document.getElementById("meta-count"),
  refreshBtn: document.getElementById("refresh-btn"),
  toast: document.getElementById("toast"),
};

function toast(msg, isErr = false) {
  els.toast.textContent = msg;
  els.toast.classList.toggle("error", isErr);
  els.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => els.toast.classList.remove("show"), 2600);
}

function escapeHtml(s = "") {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDate(str) {
  if (!str) return "—";
  try {
    const d = new Date(str);
    if (isNaN(d.getTime())) return str;
    return d.toLocaleString("zh-CN", { hour12: false });
  } catch {
    return str;
  }
}

function renderSkeleton() {
  const order = state.categoryOrder.length ? state.categoryOrder : ["health", "tech", "business"];
  els.main.innerHTML = order
    .map(
      (key) => `
    <section class="category cat-${key}">
      <div class="category-header">
        <span class="category-tag">${escapeHtml(key)}</span>
        <h2 class="category-title">加载中…</h2>
      </div>
      <div class="cards">
        <div class="skeleton-card"></div>
        <div class="skeleton-card"></div>
        <div class="skeleton-card"></div>
      </div>
    </section>`
    )
    .join("");
}

function renderEmpty() {
  els.main.innerHTML = `
    <div class="empty-state">
      <h2>还没有今天的文章</h2>
      <p>点击右上角「立即刷新」抓取最新内容，或稍后再来。</p>
    </div>`;
}

function renderCard(article) {
  const hasImg = !!article.cover_image;
  const desc = article.description
    ? escapeHtml(article.description)
    : article.word_count
    ? `${article.word_count} words · 本地可离线阅读`
    : "";
  const readerUrl = `/articles/${article.id}`;
  return `
    <article class="card" data-article-id="${article.id}">
      <a class="card-img ${hasImg ? "" : "no-img"}" href="${readerUrl}">
        ${
          hasImg
            ? `<img src="${escapeHtml(article.cover_image)}" alt="${escapeHtml(
                article.title
              )}" loading="lazy" />`
            : `<span>T</span>`
        }
      </a>
      <div class="card-body">
        <h3 class="card-title"><a href="${readerUrl}">${escapeHtml(article.title)}</a></h3>
        ${desc ? `<p class="card-desc">${desc}</p>` : ""}
        <div class="card-foot">
          <span class="card-author">${escapeHtml(article.author || "TIME")}</span>
          <a class="card-link" href="${readerUrl}">阅读 →</a>
        </div>
        <div class="card-actions" data-article-id="${article.id}">
          <button class="btn-chip chip-favorite ${article.is_favorite ? "is-on" : ""}"
                  data-action="favorite" title="收藏">
            ${article.is_favorite ? "★" : "☆"} <span>收藏</span>
          </button>
          <button class="btn-chip chip-library ${article.in_library ? "is-on" : ""}"
                  data-action="library" title="加入每日文章库">
            📚 <span>${article.in_library ? "已加入" : "加入库"}</span>
          </button>
          <button class="btn-chip chip-read ${article.is_read ? "is-on" : ""}"
                  data-action="read" title="标记已读">
            ✓ <span>${article.is_read ? "已读" : "标读"}</span>
          </button>
        </div>
      </div>
    </article>`;
}

function renderFeed() {
  if (!state.date) {
    renderEmpty();
    return;
  }
  const order = state.categoryOrder;
  const cats = state.categories;
  const hasAny = order.some((k) => (state.articles[k] || []).length);
  if (!hasAny) {
    renderEmpty();
    return;
  }
  els.main.innerHTML = order
    .map((key) => {
      const rows = state.articles[key] || [];
      const info = cats[key] || { label: key, label_cn: key };
      const cards = rows.length
        ? rows.map(renderCard).join("")
        : `<div class="empty-state" style="padding:32px 0;">暂无内容</div>`;
      return `
        <section class="category cat-${key}">
          <div class="category-header">
            <span class="category-tag">${escapeHtml(info.label)}</span>
            <h2 class="category-title">${escapeHtml(info.label_cn)}</h2>
            <span class="category-hint">${rows.length} 篇</span>
          </div>
          <div class="cards">${cards}</div>
        </section>`;
    })
    .join("");
  wireCardActions();
}

function wireCardActions() {
  els.main.querySelectorAll(".card-actions button").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const action = btn.dataset.action;
      const wrap = btn.closest(".card-actions");
      const articleId = wrap?.dataset.articleId;
      if (!articleId) return;
      try {
        const resp = await fetch(`/api/articles/${articleId}/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        if (!resp.ok) throw new Error(resp.statusText);
        const data = await resp.json();
        // update this card's buttons in place
        const articles = state.articles[data.category] || [];
        const idx = articles.findIndex((a) => a.id === data.id);
        if (idx >= 0) articles[idx] = { ...articles[idx], ...data };
        renderFeed();
        toast(
          action === "favorite"
            ? data.is_favorite ? "已收藏" : "取消收藏"
            : action === "library"
            ? data.in_library ? "已加入文章库" : "移出文章库"
            : data.is_read ? "已标记为已读" : "取消已读"
        );
      } catch (err) {
        toast("操作失败：" + err.message, true);
      }
    });
  });
}

function renderMeta() {
  els.metaDate.textContent = state.date ? `日期 ${state.date}` : "—";
  els.metaFetched.textContent = "Offline-ready · local assets";
  const total = state.categoryOrder.reduce(
    (n, k) => n + (state.articles[k] || []).length,
    0
  );
  els.metaCount.textContent = total ? `共 ${total} 篇` : "暂无数据";
}

function populateDates() {
  els.dateSelect.innerHTML = "";
  if (!state.availableDates.length) {
    const opt = document.createElement("option");
    opt.textContent = "暂无数据";
    opt.value = "";
    els.dateSelect.appendChild(opt);
    els.dateSelect.disabled = true;
    return;
  }
  els.dateSelect.disabled = false;
  state.availableDates.forEach((d) => {
    const opt = document.createElement("option");
    opt.value = d;
    opt.textContent = d;
    if (d === state.date) opt.selected = true;
    els.dateSelect.appendChild(opt);
  });
}

async function loadFeed(date = null) {
  renderSkeleton();
  try {
    const url = date ? `/api/feed?date=${encodeURIComponent(date)}` : "/api/feed";
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    state.date = data.date;
    state.availableDates = data.available_dates || [];
    state.categories = data.categories || {};
    state.categoryOrder = data.category_order || Object.keys(state.categories);
    state.articles = data.articles || {};
    populateDates();
    renderFeed();
    renderMeta();
  } catch (err) {
    toast("加载失败：" + err.message, true);
    renderEmpty();
  }
}

async function triggerRefresh() {
  els.refreshBtn.disabled = true;
  els.refreshBtn.classList.add("is-loading");
  try {
    const resp = await fetch("/api/refresh", { method: "POST" });
    const data = await resp.json();
    if (resp.ok) {
      toast(`抓取完成：${data.count} 篇（${data.date}）`);
      await loadFeed();
    } else if (resp.status === 202) {
      toast(data.message || "抓取进行中，请稍候");
    } else {
      toast("刷新失败", true);
    }
  } catch (err) {
    toast("刷新异常：" + err.message, true);
  } finally {
    els.refreshBtn.disabled = false;
    els.refreshBtn.classList.remove("is-loading");
  }
}

els.dateSelect.addEventListener("change", (e) => {
  loadFeed(e.target.value || null);
});
els.refreshBtn.addEventListener("click", triggerRefresh);

loadFeed();
