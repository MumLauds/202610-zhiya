(function () {
  "use strict";

  const source = window.ZHIYA_ADMIN_DATA;
  if (!source) {
    document.getElementById("app").innerHTML =
      '<div class="empty">没有读取到后台数据。</div>';
    return;
  }

  const books = source.books;
  const knowledge = source.knowledge;
  const STORAGE_KEY = "zhiya-admin-status";
  const state = {
    view: "dashboard",
    bookQuery: "",
    bookStatus: "all",
    bookAge: "all",
    knowledgeQuery: "",
    knowledgeStatus: "all",
    knowledgeCategory: "all",
    drawer: null,
    modal: null,
    overrides: readOverrides(),
  };

  const navItems = [
    ["dashboard", "工作台", "dashboard", null],
    ["books", "绘本管理", "book", books.length],
    ["review", "版本审核", "review", books.filter((book) => book.status !== "PUBLISHED").length],
    ["knowledge", "领域知识库", "knowledge", knowledge.filter((item) => item.status === "DRAFT").length],
    ["permissions", "接口与权限", "shield", null],
  ];

  const viewMeta = {
    dashboard: ["工作台", "集中处理绘本、版本与领域知识内容"],
    books: ["绘本管理", "维护基本信息、主题标签、适读年龄与发布状态"],
    review: ["版本审核", "检查适读性、版权来源和内容安全后发布"],
    knowledge: ["领域知识库", "审核知识来源、适用范围与 Agent 引导策略"],
    permissions: ["接口与权限", "核对角色边界、API 契约与儿童隐私要求"],
  };

  function readOverrides() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveOverrides() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.overrides));
    } catch {
      // Local file storage can be restricted; state still works for this session.
    }
  }

  function effectiveStatus(itemId) {
    return state.overrides[itemId] || null;
  }

  function bookStatus(book) {
    return effectiveStatus(`book:${book.id}`) || book.status;
  }

  function knowledgeStatus(item) {
    return effectiveStatus(`knowledge:${item.key}`) || item.status;
  }

  function icon(name) {
    const paths = {
      dashboard:
        '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
      book: '<path d="M4 5.5c3-1 5.5-.5 8 1.5v13c-2.5-2-5-2.5-8-1.5zM20 5.5c-3-1-5.5-.5-8 1.5v13c2.5-2 5-2.5 8-1.5z"/>',
      review: '<path d="M5 3h10l4 4v14H5zM15 3v5h5M8 12h8M8 16h6"/>',
      knowledge:
        '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5V5a2 2 0 0 1 2-2h14v16H6.5M8 7h8M8 10h6"/>',
      shield:
        '<path d="M12 3 20 6v5c0 5-3.4 8.4-8 10-4.6-1.6-8-5-8-10V6z"/><path d="m9 12 2 2 4-3"/>',
      leaf:
        '<path d="M20.5 3.5C12.2 3.5 5.5 7.3 5.5 14.2c0 3.4 2.6 6.3 6 6.3 6.7 0 9-8.7 9-17zM3.5 21.5c3.7-6 8-10.3 14-13.7"/>',
      search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.2 15.2 5.8 5.8"/>',
      plus: '<path d="M12 4v16M4 12h16"/>',
      close: '<path d="m5 5 14 14M19 5 5 19"/>',
      info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
      arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
      filter: '<path d="M4 5h16l-6 7v5l-4 2v-7z"/>',
      check: '<path d="m5 12 4 4L19 6"/>',
    };
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || ""}</svg>`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function statusMeta(status) {
    const map = {
      PUBLISHED: ["已发布", "published"],
      DRAFT: ["草稿", "draft"],
      IN_REVIEW: ["待审核", "review"],
      OFFLINE: ["已下线", "offline"],
    };
    return map[status] || map.DRAFT;
  }

  function statusBadge(status) {
    const [label, className] = statusMeta(status);
    return `<span class="status ${className}">${label}</span>`;
  }

  function coverMarkup(book) {
    if (!book.cover) {
      return `<div class="thumb-placeholder">版权素材<br/>待替换</div>`;
    }
    return `<img class="book-thumb" src="${book.cover}" alt="${escapeHtml(book.title)}" />`;
  }

  function showToast(message) {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("show"), 2200);
  }

  function sidebar() {
    return `
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">${icon("leaf")}</div>
          <div><strong>知芽 ADMIN</strong><small>内容管理与内容安全</small></div>
        </div>
        <div class="side-label">工作空间</div>
        <nav class="side-nav">
          ${navItems
            .map(
              ([key, label, iconName, count]) => `
              <button class="nav-item ${state.view === key ? "active" : ""}" data-action="nav" data-view="${key}">
                ${icon(iconName)}
                <span>${label}</span>
                ${count ? `<span class="nav-count">${count}</span>` : ""}
              </button>`,
            )
            .join("")}
        </nav>
        <div class="sidebar-footer">
          <strong>管理员 · 演示环境</strong>
          <small>内容操作保存在当前浏览器。未连接真实账号、数据库或模型服务。</small>
        </div>
      </aside>`;
  }

  function topbar() {
    const [title] = viewMeta[state.view];
    return `
      <header class="topbar">
        <div class="breadcrumb">知芽管理后台&nbsp;&nbsp;/&nbsp;&nbsp;<strong>${title}</strong></div>
        <div class="topbar-right">
          <span class="contract-pill">API 契约 v0.4</span>
          <div class="admin-user">
            <div class="admin-avatar">林</div>
            <div><strong>内容管理员</strong><small>管理端演示账号</small></div>
          </div>
        </div>
      </header>`;
  }

  function notice() {
    return `<div class="notice">${icon("info")}交互原型 / 非生产环境：数据来自仓库种子数据，未连接 FastAPI、SQLite、真实账号或 AI 服务。</div>`;
  }

  function pageHead() {
    const [title, subtitle] = viewMeta[state.view];
    const actions =
      state.view === "books"
        ? `<button class="btn primary" data-action="open-new-book">${icon("plus")}新增绘本</button>`
        : state.view === "knowledge"
          ? `<button class="btn primary" data-action="open-new-knowledge">${icon("plus")}新增知识</button>`
          : state.view === "review"
            ? `<button class="btn soft" data-action="nav" data-view="books">${icon("book")}返回绘本管理</button>`
            : "";
    return `
      <div class="page-head">
        <div class="page-title"><h1>${title}</h1><p>${subtitle}</p></div>
        <div class="page-actions">${actions}</div>
      </div>`;
  }

  function dashboardView() {
    const stats = [
      [books.length, "绘本本体", "以 storybookId 去重"],
      [books.filter((book) => bookStatus(book) === "PUBLISHED").length, "当前已发布", "仅计算 PUBLISHED 版本"],
      [books.filter((book) => bookStatus(book) !== "PUBLISHED").length, "待审核版本", "草稿不进入公开书架"],
      [knowledge.filter((item) => knowledgeStatus(item) === "DRAFT").length, "待审核知识条目", "最新版本处于 DRAFT"],
    ];
    const tasks = [
      ["《逃家小兔》版权来源需要复核", "公开出版作品不能因课程演示自动视为可发布", "高优先级"],
      [`${knowledge.filter((item) => knowledgeStatus(item) === "DRAFT").length} 条领域知识仍待审核`, "需要补充来源、适龄范围与任务类型", "待处理"],
      ["封面映射与真实资源需要保持一致", "PNG 路径应与仓库 JPG 资源统一", "普通"],
    ];
    return `
      ${notice()}
      ${pageHead()}
      <section class="stat-grid">
        ${stats
          .map(
            ([value, label, note]) => `
            <article class="stat-card"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`,
          )
          .join("")}
      </section>
      <div class="dashboard-grid">
        <section class="panel">
          <div class="panel-head"><h2>待处理事项</h2><button class="btn soft" data-action="nav" data-view="review">去版本审核</button></div>
          <div class="task-list">
            ${tasks
              .map(
                ([title, body, tag]) => `
                <div class="task"><span class="task-dot"></span><div><strong>${title}</strong><p>${body}</p></div><em>${tag}</em></div>`,
              )
              .join("")}
          </div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>快速操作</h2></div>
          <div class="quick-grid">
            <button class="quick-item" data-action="nav" data-view="books">${icon("book")}<strong>绘本资料</strong><small>检索、查看、新建</small></button>
            <button class="quick-item" data-action="nav" data-view="review">${icon("review")}<strong>版本审核</strong><small>发布、下线、撤回</small></button>
            <button class="quick-item" data-action="nav" data-view="knowledge">${icon("knowledge")}<strong>领域知识</strong><small>修订、审核、历史</small></button>
            <button class="quick-item" data-action="nav" data-view="permissions">${icon("shield")}<strong>接口与权限</strong><small>核对管理边界</small></button>
          </div>
          <div class="flow-note">推荐审核顺序：录入草稿 → 检查素材版权 → 核对适龄与分页 → 批准发布</div>
        </section>
      </div>`;
  }

  function filteredBooks() {
    const query = state.bookQuery.trim().toLowerCase();
    return books.filter((book) => {
      const status = bookStatus(book);
      const statusMatch = state.bookStatus === "all" || status === state.bookStatus;
      const ageMatch = state.bookAge === "all" || book.age === state.bookAge;
      const text = `${book.bookNo} ${book.title} ${book.themes.join(" ")}`.toLowerCase();
      return statusMatch && ageMatch && (!query || text.includes(query));
    });
  }

  function bookRows() {
    const items = filteredBooks();
    if (!items.length) return '<tr><td colspan="7"><div class="empty">没有匹配的绘本。</div></td></tr>';
    return items
      .map(
        (book) => `
        <tr>
          <td>${book.bookNo}</td>
          <td>
            <div class="book-cell">
              ${coverMarkup(book)}
              <div><strong>${escapeHtml(book.title)}</strong><small>版本 v${book.versionNo} · ${book.pageCount} 页</small></div>
            </div>
          </td>
          <td>${escapeHtml(book.age)}</td>
          <td><div class="tag-list">${book.themes.slice(0, 2).map((theme) => `<span class="tag">${escapeHtml(theme)}</span>`).join("")}</div></td>
          <td>${statusBadge(bookStatus(book))}</td>
          <td>${book.sourceType}</td>
          <td><button class="link-btn" data-action="open-book" data-id="${book.id}">查看详情</button></td>
        </tr>`,
      )
      .join("");
  }

  function booksTableMarkup() {
    return `
      <div class="table-card panel">
        <table>
          <thead><tr><th>编号</th><th>绘本名称</th><th>适读年龄</th><th>主题标签</th><th>当前状态</th><th>素材来源</th><th>操作</th></tr></thead>
          <tbody id="book-table-body">${bookRows()}</tbody>
        </table>
      </div>`;
  }

  function booksView() {
    return `
      ${notice()}
      ${pageHead()}
      <div class="toolbar">
        <label class="search-field">${icon("search")}<input id="book-search" value="${escapeHtml(state.bookQuery)}" placeholder="搜索书名、编号或主题" /></label>
        <select class="select" id="book-status">
          <option value="all" ${state.bookStatus === "all" ? "selected" : ""}>全部状态</option>
          <option value="PUBLISHED" ${state.bookStatus === "PUBLISHED" ? "selected" : ""}>已发布</option>
          <option value="DRAFT" ${state.bookStatus === "DRAFT" ? "selected" : ""}>草稿</option>
          <option value="IN_REVIEW" ${state.bookStatus === "IN_REVIEW" ? "selected" : ""}>待审核</option>
          <option value="OFFLINE" ${state.bookStatus === "OFFLINE" ? "selected" : ""}>已下线</option>
        </select>
        <select class="select" id="book-age">
          <option value="all">全部年龄</option>
          ${[...new Set(books.map((book) => book.age))].map((age) => `<option value="${escapeHtml(age)}" ${state.bookAge === age ? "selected" : ""}>${escapeHtml(age)}</option>`).join("")}
        </select>
        <span class="result-count" id="book-count">共 ${filteredBooks().length} 本</span>
      </div>
      <div id="book-table-wrap">${booksTableMarkup()}</div>`;
  }

  function reviewRows() {
    return books
      .filter((book) => bookStatus(book) !== "PUBLISHED")
      .map(
        (book) => `
        <tr>
          <td>B-${String(book.id).padStart(3, "0")}</td>
          <td><div class="book-cell"><div><strong>${escapeHtml(book.title)}</strong><small>版本 v${book.versionNo}</small></div></div></td>
          <td>${escapeHtml(book.age)}</td>
          <td>${book.sourceType}</td>
          <td>${statusBadge(bookStatus(book))}</td>
          <td><button class="link-btn" data-action="open-review" data-id="${book.id}">进入审核</button></td>
        </tr>`,
      )
      .join("") || '<tr><td colspan="6"><div class="empty">没有待审核版本。</div></td></tr>';
  }

  function reviewView() {
    return `
      ${notice()}
      ${pageHead()}
      <div class="panel table-card">
        <table>
          <thead><tr><th>版本编号</th><th>绘本版本</th><th>适读年龄</th><th>素材来源</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>${reviewRows()}</tbody>
        </table>
      </div>`;
  }

  function filteredKnowledge() {
    const query = state.knowledgeQuery.trim().toLowerCase();
    return knowledge.filter((item) => {
      const status = knowledgeStatus(item);
      const categoryMatch =
        state.knowledgeCategory === "all" || item.category === state.knowledgeCategory;
      const statusMatch = state.knowledgeStatus === "all" || status === state.knowledgeStatus;
      const text = `${item.key} ${item.title} ${item.content}`.toLowerCase();
      return statusMatch && categoryMatch && (!query || text.includes(query));
    });
  }

  function knowledgeRows() {
    const items = filteredKnowledge();
    if (!items.length) return '<tr><td colspan="7"><div class="empty">没有匹配的知识条目。</div></td></tr>';
    return items
      .map(
        (item) => `
        <tr>
          <td>${item.key}</td>
          <td><div class="book-cell"><div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.content).slice(0, 48)}...</small></div></div></td>
          <td>${escapeHtml(item.category)}</td>
          <td>${escapeHtml(item.age)}</td>
          <td>v${item.version}</td>
          <td>${statusBadge(knowledgeStatus(item))}</td>
          <td><button class="link-btn" data-action="open-knowledge" data-key="${escapeHtml(item.key)}">查看 / 审核</button></td>
        </tr>`,
      )
      .join("");
  }

  function knowledgeTableMarkup() {
    return `
      <div class="panel table-card">
        <table>
          <thead><tr><th>知识编号</th><th>条目标题与摘要</th><th>类别</th><th>适用年龄</th><th>版本</th><th>审核状态</th><th>操作</th></tr></thead>
          <tbody>${knowledgeRows()}</tbody>
        </table>
      </div>`;
  }

  function knowledgeView() {
    return `
      ${notice()}
      ${pageHead()}
      <div class="toolbar">
        <label class="search-field">${icon("search")}<input id="knowledge-search" value="${escapeHtml(state.knowledgeQuery)}" placeholder="搜索知识编号、标题或正文" /></label>
        <select class="select" id="knowledge-status">
          <option value="all">全部审核状态</option>
          <option value="DRAFT" ${state.knowledgeStatus === "DRAFT" ? "selected" : ""}>草稿</option>
          <option value="IN_REVIEW" ${state.knowledgeStatus === "IN_REVIEW" ? "selected" : ""}>待审核</option>
          <option value="PUBLISHED" ${state.knowledgeStatus === "PUBLISHED" ? "selected" : ""}>已通过</option>
        </select>
        <select class="select" id="knowledge-category">
          <option value="all">全部类别</option>
          ${[...new Set(knowledge.map((item) => item.category))].map((category) => `<option value="${escapeHtml(category)}" ${state.knowledgeCategory === category ? "selected" : ""}>${escapeHtml(category)}</option>`).join("")}
        </select>
        <span class="result-count">共 ${filteredKnowledge().length} 条</span>
      </div>
      <div id="knowledge-table-wrap">${knowledgeTableMarkup()}</div>`;
  }

  function permissionsView() {
    return `
      ${notice()}
      ${pageHead()}
      <div class="permission-grid">
        <article class="permission-card">${icon("shield")}<h3>家长用户</h3><p>创建孩子档案、选择绘本、发起共读、确认 AI 生成内容，并管理本人阅读记录。</p></article>
        <article class="permission-card">${icon("book")}<h3>儿童模式</h3><p>仅展示家长授权的已审核绘本，允许大按钮翻页、听读和限时选项，不能修改家长设置。</p></article>
        <article class="permission-card">${icon("knowledge")}<h3>内容管理员</h3><p>维护绘本、版本、领域知识与发布状态，不读取儿童原始录音和其他家庭隐私数据。</p></article>
      </div>
      <div class="panel table-card">
        <table>
          <thead><tr><th>方法</th><th>接口路径</th><th>用途</th><th>权限边界</th></tr></thead>
          <tbody>
            <tr><td>POST</td><td>/api/v1/admin/storybooks</td><td>创建绘本草稿与元数据（API-027）</td><td>内容管理员</td></tr>
            <tr><td>PATCH</td><td>/api/v1/admin/story-versions/{version_id}</td><td>修改版本内容与发布状态（API-028）</td><td>内容管理员</td></tr>
            <tr><td>GET</td><td>/api/v1/admin/knowledge-items</td><td>查询知识管理列表与来源（API-029）</td><td>内容管理员</td></tr>
            <tr><td>POST</td><td>/api/v1/admin/knowledge-items</td><td>新增知识条目（API-030）</td><td>内容管理员</td></tr>
            <tr><td>PATCH</td><td>/api/v1/admin/knowledge-items/{knowledge_id}</td><td>修订知识内容与审核状态（API-031）</td><td>内容管理员</td></tr>
            <tr><td>POST</td><td>/api/v1/reading-sessions/{session_id}/guidance</td><td>生成当前页受控共读引导（API-017）</td><td>本人家长或绑定会话儿童</td></tr>
            <tr><td>POST</td><td>/api/v1/reading-sessions/{session_id}/completions</td><td>结束阅读并生成小结（API-019）</td><td>本人家长或绑定会话儿童</td></tr>
          </tbody>
        </table>
      </div>
      <p class="notice">以上为接口契约约定。当前原型仅演示；截至 2026-11-17，后端已实现家长结束阅读，儿童凭证、引导及管理员接口尚未实现。受保护写请求须校验 CSRF，权限仍由服务端核验。</p>`;
  }

  function currentView() {
    if (state.view === "dashboard") return dashboardView();
    if (state.view === "books") return booksView();
    if (state.view === "review") return reviewView();
    if (state.view === "knowledge") return knowledgeView();
    return permissionsView();
  }

  function bookDrawer(book) {
    return `
      <div class="drawer-backdrop" data-action="close-drawer">
        <aside class="drawer" data-panel>
          <div class="drawer-head">
            <div><h2>${escapeHtml(book.title)}</h2><p>${book.bookNo} · 当前版本 v${book.versionNo}</p></div>
            <button class="icon-close" data-action="close-drawer">${icon("close")}</button>
          </div>
          <div class="drawer-body">
            <div class="drawer-cover">${book.cover ? `<img src="${book.cover}" alt="">` : `<div class="thumb-placeholder">暂无可用封面</div>`}</div>
            <h2 class="detail-title">${escapeHtml(book.title)}</h2>
            <div class="meta">${escapeHtml(book.age)} · ${book.pageCount} 页 · ${escapeHtml(book.sourceType)}<br/>${statusBadge(bookStatus(book))}</div>
            <div class="detail-section"><h3>主题标签</h3><div class="tag-list">${book.themes.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div></div>
            <div class="detail-section"><h3>版权与素材</h3><div class="detail-block">${escapeHtml(book.licenseNote)}</div></div>
            <div class="detail-section"><h3>页面内容</h3><div class="page-list">${
              book.pages.length
                ? book.pages
                    .map(
                      (page) => `<div class="page-item"><strong>第 ${page.page} 页</strong><p>${escapeHtml(page.text)}</p></div>`,
                    )
                    .join("")
                : '<div class="detail-block">版权素材待复核，本版本暂无公开页面内容。</div>'
            }</div></div>
          </div>
          <div class="drawer-actions">
            <button class="btn soft" data-action="open-review" data-id="${book.id}">进入审核</button>
            <button class="btn primary" data-action="publish-book" data-id="${book.id}">批准发布</button>
          </div>
        </aside>
      </div>`;
  }

  function knowledgeDrawer(item) {
    return `
      <div class="drawer-backdrop" data-action="close-drawer">
        <aside class="drawer" data-panel>
          <div class="drawer-head">
            <div><h2>${escapeHtml(item.key)}</h2><p>${escapeHtml(item.category)} · ${escapeHtml(item.age)} · v${item.version}</p></div>
            <button class="icon-close" data-action="close-drawer">${icon("close")}</button>
          </div>
          <div class="drawer-body">
            <h2 class="detail-title">${escapeHtml(item.title)}</h2>
            <div class="meta">${statusBadge(knowledgeStatus(item))} · 任务类型：${escapeHtml(item.tasks.join("、") || "通用")}</div>
            <div class="detail-section"><h3>知识正文</h3><div class="detail-block">${escapeHtml(item.content)}</div></div>
            <div class="detail-section"><h3>来源</h3><div class="detail-block">${escapeHtml(item.source)}<br/>${escapeHtml(item.locator)}</div></div>
            <div class="detail-section"><h3>质量检查</h3><div class="detail-block">来源可追溯 · 适龄标签已填写 · Agent 任务类型已声明</div></div>
          </div>
          <div class="drawer-actions">
            <button class="btn soft" data-action="close-drawer">暂不处理</button>
            <button class="btn primary" data-action="approve-knowledge" data-key="${escapeHtml(item.key)}">审核通过</button>
          </div>
        </aside>
      </div>`;
  }

  function modalMarkup() {
    if (!state.modal) return "";
    const config = {
      newBook: ["新增绘本", "填写绘本基本信息，保存后进入草稿状态。"],
      newKnowledge: ["新增知识条目", "新增内容必须记录来源、适用年龄和 Agent 任务类型。"],
      review: ["版本审核", "确认适龄性、版权来源、页面完整性和内容安全。"],
    }[state.modal];
    return `
      <div class="modal-backdrop" data-action="close-modal">
        <section class="modal" data-panel>
          <div class="modal-head"><h2>${config[0]}</h2><button class="icon-close" data-action="close-modal">${icon("close")}</button></div>
          <div class="modal-body">
            <p>${config[1]}</p>
            ${
              state.modal === "review"
                ? '<div class="detail-block">审核检查项：素材版权、适龄标签、页面数量、互动问题与内容安全。</div>'
                : `<div class="form-grid">
                    <div class="field"><label>${state.modal === "newBook" ? "绘本名称" : "知识标题"}</label><input placeholder="输入标题" /></div>
                    <div class="field"><label>${state.modal === "newBook" ? "适读年龄" : "适用年龄"}</label><select><option>3–4 岁</option><option>4–5 岁</option><option>5–6 岁</option></select></div>
                    <div class="field full"><label>${state.modal === "newBook" ? "主题标签" : "知识正文"}</label><textarea placeholder="填写内容"></textarea></div>
                    <div class="field full"><label>来源与版权说明</label><input placeholder="填写来源、作者或素材许可" /></div>
                  </div>`
            }
          </div>
          <div class="modal-actions">
            <button class="btn" data-action="close-modal">取消</button>
            <button class="btn primary" data-action="submit-modal">${state.modal === "review" ? "审核通过并发布" : "保存草稿"}</button>
          </div>
        </section>
      </div>`;
  }

  function drawerMarkup() {
    if (!state.drawer) return "";
    if (state.drawer.type === "book") {
      const book = books.find((item) => item.id === state.drawer.id);
      return book ? bookDrawer(book) : "";
    }
    const item = knowledge.find((entry) => entry.key === state.drawer.key);
    return item ? knowledgeDrawer(item) : "";
  }

  function render() {
    const app = document.getElementById("app");
    app.innerHTML = `
      <div class="admin-shell">
        ${sidebar()}
        <div class="workspace">
          ${topbar()}
          <main class="content">${currentView()}</main>
        </div>
      </div>
      ${drawerMarkup()}
      ${modalMarkup()}`;
  }

  function updateBooksContent() {
    const table = document.getElementById("book-table-wrap");
    const count = document.getElementById("book-count");
    if (table) table.innerHTML = booksTableMarkup();
    if (count) count.textContent = `共 ${filteredBooks().length} 本`;
  }

  function updateKnowledgeContent() {
    const table = document.getElementById("knowledge-table-wrap");
    if (table) table.innerHTML = knowledgeTableMarkup();
  }

  function handleAction(action, target) {
    if (action === "nav") {
      state.view = target.dataset.view;
      state.drawer = null;
      state.modal = null;
      render();
      return;
    }
    if (action === "open-book") {
      state.drawer = { type: "book", id: Number(target.dataset.id) };
      render();
      return;
    }
    if (action === "open-knowledge") {
      state.drawer = { type: "knowledge", key: target.dataset.key };
      render();
      return;
    }
    if (action === "close-drawer") {
      if (target.closest("[data-panel]") && !target.classList.contains("drawer-backdrop")) {
        if (!target.classList.contains("icon-close") && !target.classList.contains("btn")) return;
      }
      state.drawer = null;
      render();
      return;
    }
    if (action === "open-review") {
      state.drawer = null;
      state.modal = "review";
      render();
      return;
    }
    if (action === "open-new-book") {
      state.modal = "newBook";
      render();
      return;
    }
    if (action === "open-new-knowledge") {
      state.modal = "newKnowledge";
      render();
      return;
    }
    if (action === "close-modal") {
      if (target.closest("[data-panel]") && !target.classList.contains("modal-backdrop")) {
        if (!target.classList.contains("icon-close") && !target.classList.contains("btn")) return;
      }
      state.modal = null;
      render();
      return;
    }
    if (action === "submit-modal") {
      state.modal = null;
      render();
      showToast("操作已保存到本地演示状态");
      return;
    }
    if (action === "publish-book") {
      state.overrides[`book:${target.dataset.id}`] = "PUBLISHED";
      saveOverrides();
      state.drawer = null;
      render();
      showToast("绘本已批准发布");
      return;
    }
    if (action === "approve-knowledge") {
      state.overrides[`knowledge:${target.dataset.key}`] = "PUBLISHED";
      saveOverrides();
      state.drawer = null;
      render();
      showToast("知识条目已审核通过");
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) {
      if (event.target.classList.contains("drawer-backdrop")) {
        state.drawer = null;
        render();
      }
      if (event.target.classList.contains("modal-backdrop")) {
        state.modal = null;
        render();
      }
      return;
    }
    if (
      ["close-drawer", "close-modal"].includes(target.dataset.action) &&
      event.target.closest("[data-panel]") &&
      !event.target.closest(".icon-close")
    ) {
      return;
    }
    handleAction(target.dataset.action, target);
  });

  document.addEventListener("input", (event) => {
    if (event.target.id === "book-search") {
      state.bookQuery = event.target.value;
      updateBooksContent();
    }
    if (event.target.id === "knowledge-search") {
      state.knowledgeQuery = event.target.value;
      updateKnowledgeContent();
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.id === "book-status") {
      state.bookStatus = event.target.value;
      updateBooksContent();
    }
    if (event.target.id === "book-age") {
      state.bookAge = event.target.value;
      updateBooksContent();
    }
    if (event.target.id === "knowledge-status") {
      state.knowledgeStatus = event.target.value;
      updateKnowledgeContent();
    }
    if (event.target.id === "knowledge-category") {
      state.knowledgeCategory = event.target.value;
      updateKnowledgeContent();
    }
  });

  render();
})();
