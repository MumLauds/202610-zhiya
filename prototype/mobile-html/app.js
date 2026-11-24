(() => {
  "use strict";

  const STORAGE_KEY = "zhiya_html_state_v1";
  const PINYIN = window.ZHIYA_PINYIN || {};
  const seedBooks = (window.ZHIYA_BOOKS && window.ZHIYA_BOOKS.books) || [];
  const AGE_OPTIONS = ["全部", "3–4 岁", "4–5 岁", "5–6 岁"];
  const THEME_OPTIONS = ["全部", "亲子依恋", "勇气", "友谊", "自然观察", "情绪安抚", "想象", "分享", "生命成长", "合作", "习惯"];
  const SHELF_CATEGORIES = ["睡前故事", "成长启蒙", "自然探索", "亲子共读"];

  const defaults = {
    shelfIds: [2, 3, 4],
    shelfCategories: {},
    showPinyin: true,
    records: [],
    customBooks: [],
  };

  function loadState() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      return {
        ...defaults,
        ...saved,
        shelfIds: Array.isArray(saved.shelfIds) ? saved.shelfIds : defaults.shelfIds.slice(),
        shelfCategories: saved.shelfCategories && typeof saved.shelfCategories === "object" ? saved.shelfCategories : {},
        records: Array.isArray(saved.records) ? saved.records : [],
        customBooks: Array.isArray(saved.customBooks) ? saved.customBooks : [],
      };
    } catch (error) {
      return { ...defaults, shelfIds: defaults.shelfIds.slice() };
    }
  }

  let appState = loadState();
  let allBooks = [];
  let currentView = "home";
  let currentBook = null;
  let currentPageIndex = 0;
  let readingStartedAt = 0;
  let libraryQuery = "";
  let activeAge = "全部";
  let activeTheme = "全部";
  let shelfManage = false;
  let shelfFilter = "全部";
  let selectedShelfIds = new Set();
  let draftBook = null;
  let toastTimer = 0;

  const $ = (id) => document.getElementById(id);
  const topbar = $("topbar");
  const bottomNav = $("bottomNav");
  const mainScroll = $("mainScroll");
  const audio = new Audio();
  audio.preload = "metadata";

  function escapeHTML(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(appState));
    } catch (error) {
      showToast("本地存储不可用");
    }
  }

  function decorateBook(book) {
    return {
      ...book,
      themes: Array.isArray(book.themes) ? book.themes : [],
      pages: Array.isArray(book.pages) ? book.pages : [],
      themeText: (book.themes || []).join(" · "),
      pageCount: (book.pages || []).length,
      firstText: book.pages && book.pages.length ? book.pages[0].text : "",
    };
  }

  function rebuildBooks() {
    allBooks = seedBooks.concat(appState.customBooks || []).map(decorateBook);
  }

  function hasShelf(bookId) {
    return appState.shelfIds.includes(bookId);
  }

  function toast(message) {
    const element = $("toast");
    window.clearTimeout(toastTimer);
    element.textContent = message;
    element.classList.add("show");
    toastTimer = window.setTimeout(() => element.classList.remove("show"), 1800);
  }
  const showToast = toast;

  function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const minutes = Math.floor(seconds / 60);
    const rest = Math.floor(seconds % 60).toString().padStart(2, "0");
    return `${minutes}:${rest}`;
  }

  function formatDate(timestamp) {
    const date = new Date(timestamp);
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function setActiveNav(tab) {
    document.querySelectorAll("#bottomNav button").forEach((button) => {
      button.classList.toggle("active", button.dataset.nav === tab);
    });
  }

  function showView(view, options = {}) {
    const tabViews = ["home", "library", "original", "growth", "profile"];
    const isTabView = tabViews.includes(view);
    if (currentView === "reader" && view !== "reader") stopAudio();
    currentView = view;
    document.querySelectorAll(".view").forEach((section) => {
      section.classList.toggle("active", section.dataset.view === view);
    });
    topbar.hidden = !isTabView;
    bottomNav.hidden = !isTabView;
    const tab = options.tab || (isTabView ? view : currentBook ? "library" : "home");
    setActiveNav(tab);
    renderView(view);
    if (!options.keepScroll) mainScroll.scrollTop = 0;
  }

  function renderView(view) {
    if (view === "home") renderHome();
    if (view === "library") renderLibrary();
    if (view === "detail") renderDetail();
    if (view === "reader") renderReader();
    if (view === "growth") renderGrowth();
    if (view === "profile") renderProfile();
    if (view === "shelf") renderShelf();
  }

  function bookTile(book) {
    return `
      <button class="book-tile" data-book-id="${book.id}">
        <img src="${escapeHTML(book.cover || "assets/covers/b002.webp")}" alt="${escapeHTML(book.title)}">
        <strong>${escapeHTML(book.title)}</strong>
        <small>${escapeHTML(book.age)} · ${book.pageCount} 页</small>
      </button>`;
  }

  function libraryCard(book) {
    return `
      <button class="library-card" data-book-id="${book.id}">
        <img src="${escapeHTML(book.cover || "assets/covers/b002.webp")}" alt="${escapeHTML(book.title)}">
        <strong>${escapeHTML(book.title)}</strong>
        <small>${escapeHTML(book.age)} · ${escapeHTML(book.themeText)}</small>
      </button>`;
  }

  function renderHome() {
    const hero = allBooks.find((book) => book.id === 2) || allBooks[0];
    if (!hero) return;
    $("heroCover").src = hero.cover;
    $("heroCover").alt = hero.title;
    $("heroAge").textContent = `今日共读 · ${hero.age}`;
    $("heroChipCover").src = hero.cover;
    $("heroChipCover").alt = hero.title;
    $("heroChipTitle").textContent = hero.title;
    $("heroChipMeta").textContent = `${hero.age} · ${hero.pageCount} 页`;
    $("homeBooks").innerHTML = allBooks.slice(0, 6).map(bookTile).join("");
    $("heroRead").onclick = () => openBook(hero.id);
    $("heroChip").onclick = () => openBook(hero.id);
    $("planRead").onclick = () => openBook(hero.id);
  }

  function renderLibraryFilters() {
    $("ageFilters").innerHTML = AGE_OPTIONS.map((age) => `
      <button class="chip ${activeAge === age ? "active" : ""}" data-age="${age}">${age}</button>
    `).join("");
    $("themeFilters").innerHTML = THEME_OPTIONS.map((theme) => `
      <button class="chip ${activeTheme === theme ? "active" : ""}" data-theme="${theme}">${theme}</button>
    `).join("");
  }

  function filteredLibraryBooks() {
    const query = libraryQuery.trim().toLowerCase();
    return allBooks.filter((book) => {
      const ageMatch = activeAge === "全部" || book.age === activeAge;
      const themeMatch = activeTheme === "全部" || book.themes.includes(activeTheme);
      const text = `${book.title} ${book.bookNo || ""} ${book.themeText}`.toLowerCase();
      return ageMatch && themeMatch && (!query || text.includes(query));
    });
  }

  function renderLibraryResults() {
    const books = filteredLibraryBooks();
    $("libraryCount").textContent = books.length === allBooks.length ? `${allBooks.length} 本原创绘本` : `${books.length} / ${allBooks.length} 本绘本`;
    $("libraryGrid").innerHTML = books.map(libraryCard).join("");
    $("libraryEmpty").hidden = books.length > 0;
  }

  function renderLibrary() {
    $("searchInput").value = libraryQuery;
    renderLibraryFilters();
    renderLibraryResults();
  }

  function openBook(bookId) {
    const book = allBooks.find((item) => item.id === Number(bookId));
    if (!book) return;
    currentBook = book;
    currentPageIndex = 0;
    renderDetail();
    showView("detail", { tab: "library" });
  }

  function renderDetail() {
    if (!currentBook) {
      showView("library", { tab: "library" });
      return;
    }
    $("detailBookNo").textContent = currentBook.bookNo || "家庭原创";
    $("detailCover").src = currentBook.cover || "assets/covers/b002.webp";
    $("detailCover").alt = currentBook.title;
    $("detailTitle").textContent = currentBook.title;
    $("detailMeta").textContent = `${currentBook.age} · ${currentBook.difficulty || "自定义"} · ${currentBook.pageCount} 页`;
    $("detailSummary").textContent = currentBook.firstText || "这一页故事等待你和孩子一起打开。";
    $("detailReason").textContent = `适合 ${currentBook.age} 的亲子共读，主题包括 ${currentBook.themeText || "成长与想象"}。`;
    $("detailTags").innerHTML = (currentBook.themes || []).map((theme) => `<span class="tag">${escapeHTML(theme)}</span>`).join("");
    const inShelf = hasShelf(currentBook.id);
    $("detailShelf").textContent = inShelf ? "已收藏" : "收藏";
    $("detailShelfAction").textContent = inShelf ? "移出书架" : "加入书架";
  }

  function toggleCurrentShelf() {
    if (!currentBook) return;
    const ids = new Set(appState.shelfIds);
    const categories = { ...appState.shelfCategories };
    if (ids.has(currentBook.id)) {
      ids.delete(currentBook.id);
      delete categories[currentBook.id];
      toast("已移出书架");
    } else {
      ids.add(currentBook.id);
      toast("已加入书架");
    }
    appState.shelfIds = Array.from(ids);
    appState.shelfCategories = categories;
    saveState();
    renderDetail();
  }

  function startReading(book = currentBook) {
    if (!book) return;
    currentBook = book;
    currentPageIndex = 0;
    readingStartedAt = Date.now();
    stopAudio();
    renderReader();
    showView("reader", { tab: "library" });
  }

  function makeReaderTokens(page) {
    return (page.tokens || []).map((token, index) => `
      <span class="ruby-token" data-token-index="${index}">
        <small>${escapeHTML(token.pinyin || "")}</small>
        <b>${escapeHTML(token.char || "")}</b>
      </span>
    `).join("");
  }

  function renderReader() {
    if (!currentBook || !currentBook.pages.length) return;
    currentPageIndex = Math.max(0, Math.min(currentPageIndex, currentBook.pages.length - 1));
    const page = currentBook.pages[currentPageIndex];
    $("readerTitle").textContent = currentBook.title;
    $("readerProgressText").textContent = `${currentPageIndex + 1} / ${currentBook.pages.length}`;
    $("readerImage").src = page.image || currentBook.cover;
    $("readerImage").alt = `${currentBook.title} 第 ${currentPageIndex + 1} 页`;
    $("sceneIndex").textContent = `第 ${currentPageIndex + 1} 幕`;
    $("sceneLabel").textContent = page.scene || "绘本画面";
    $("rubyGrid").innerHTML = makeReaderTokens(page);
    $("rubyGrid").classList.toggle("hide-pinyin", !appState.showPinyin);
    $("pinyinHint").textContent = `逐字拼音 · ${appState.showPinyin ? "已开启" : "已关闭"}`;
    $("pinyinToggle").textContent = appState.showPinyin ? "隐藏拼音" : "显示拼音";
    $("questionLabel").textContent = `共读小问题 · ${page.promptType || "观察"}`;
    $("questionText").textContent = page.prompt || "你从这一页看到了什么？";
    const lastPage = currentPageIndex === currentBook.pages.length - 1;
    $("nextPage").textContent = lastPage ? "完成阅读" : "下一页 →";
    $("nextPageIcon").disabled = lastPage;
    $("readerProgressBar").style.width = `${((currentPageIndex + 1) / currentBook.pages.length) * 100}%`;
    $("audioTitle").textContent = "听知芽读这一页";
    $("audioTime").textContent = "0:00";
    $("audioProgressBar").style.width = "0%";
    $("playButton").textContent = "▶";
    document.querySelectorAll(".ruby-token").forEach((token) => token.classList.remove("spoken"));
  }

  function setPage(index) {
    if (!currentBook) return;
    stopAudio();
    currentPageIndex = index;
    renderReader();
    $("rubyGrid").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function goPrevPage() {
    if (currentPageIndex <= 0) {
      renderDetail();
      showView("detail", { tab: "library" });
      return;
    }
    setPage(currentPageIndex - 1);
  }

  function goNextPage() {
    if (!currentBook) return;
    if (currentPageIndex >= currentBook.pages.length - 1) {
      finishReading();
      return;
    }
    setPage(currentPageIndex + 1);
  }

  function finishReading() {
    if (!currentBook) return;
    stopAudio();
    const minutes = Math.max(1, Math.round((Date.now() - readingStartedAt) / 60000));
    const record = {
      id: `record-${Date.now()}`,
      bookId: currentBook.id,
      title: currentBook.title,
      cover: currentBook.cover,
      minutes,
      pages: currentBook.pages.length,
      date: formatDate(Date.now()),
      timestamp: Date.now(),
    };
    appState.records = [record].concat(appState.records).slice(0, 50);
    saveState();
    $("summaryTitle").textContent = `《${currentBook.title}》`;
    $("summaryMinutes").textContent = String(minutes);
    $("summaryPages").textContent = String(currentBook.pages.length);
    showView("summary", { tab: "growth" });
  }

  function togglePinyin() {
    appState.showPinyin = !appState.showPinyin;
    saveState();
    renderReader();
    toast(appState.showPinyin ? "拼音已显示" : "拼音已隐藏");
  }

  function syncAudioButton() {
    $("playButton").textContent = audio.paused ? "▶" : "Ⅱ";
    $("audioTitle").textContent = audio.paused ? "听知芽读这一页" : "正在朗读";
  }

  function playCurrentPageAudio() {
    if (!currentBook) return;
    const page = currentBook.pages[currentPageIndex];
    if (!page || !page.audio) {
      toast("这一页暂时没有配音");
      return;
    }
    if (!audio.paused) {
      audio.pause();
      return;
    }
    const resolved = new URL(page.audio, window.location.href).href;
    if (audio.src !== resolved) {
      audio.src = resolved;
      audio.currentTime = 0;
    }
    audio.play().catch(() => toast("朗读音频播放失败"));
  }

  function stopAudio() {
    if (!audio.paused) audio.pause();
    try { audio.currentTime = 0; } catch (error) { /* no source loaded */ }
    if ($("playButton")) $("playButton").textContent = "▶";
    if ($("audioTitle")) $("audioTitle").textContent = "听知芽读这一页";
    if ($("audioProgressBar")) $("audioProgressBar").style.width = "0%";
    if ($("audioTime")) $("audioTime").textContent = "0:00";
    document.querySelectorAll(".ruby-token").forEach((token) => token.classList.remove("spoken"));
  }

  audio.addEventListener("play", syncAudioButton);
  audio.addEventListener("pause", syncAudioButton);
  audio.addEventListener("ended", () => {
    syncAudioButton();
    document.querySelectorAll(".ruby-token").forEach((token) => token.classList.remove("spoken"));
  });
  audio.addEventListener("error", () => toast("朗读音频加载失败"));
  audio.addEventListener("timeupdate", () => {
    const page = currentBook && currentBook.pages[currentPageIndex];
    if (!page || !page.tokens || !page.tokens.length) return;
    const duration = audio.duration;
    const ratio = Number.isFinite(duration) && duration > 0 ? Math.min(audio.currentTime / duration, 1) : 0;
    const tokenIndex = Math.min(page.tokens.length - 1, Math.floor(ratio * page.tokens.length));
    document.querySelectorAll(".ruby-token").forEach((token, index) => {
      token.classList.toggle("spoken", index === tokenIndex);
    });
    $("audioProgressBar").style.width = `${ratio * 100}%`;
    $("audioTime").textContent = formatTime(audio.currentTime);
  });

  function renderGrowth() {
    const records = appState.records || [];
    const minutes = records.reduce((sum, item) => sum + Number(item.minutes || 0), 0);
    const days = new Set(records.map((item) => item.date)).size;
    const books = new Set(records.map((item) => item.bookId)).size;
    $("growthMinutes").textContent = `${minutes} 分钟`;
    $("growthDays").textContent = `已记录 ${days} 个阅读日`;
    $("growthCount").textContent = String(records.length);
    $("growthBooks").textContent = String(books);
    $("recordCount").textContent = `${records.length} 条`;
    $("recordList").innerHTML = records.map((record) => `
      <button class="record-row" data-record-book-id="${record.bookId}">
        <img src="${escapeHTML(record.cover || "assets/covers/b002.webp")}" alt="">
        <span>
          <strong>${escapeHTML(record.title)}</strong>
          <small>${escapeHTML(record.date)} · ${record.minutes} 分钟 · ${record.pages} 页</small>
        </span>
      </button>
    `).join("");
    $("growthEmpty").hidden = records.length > 0;
    $("recordList").hidden = records.length === 0;
  }

  function renderProfile() {
    $("profileShelfCount").textContent = `${appState.shelfIds.length} 本 →`;
    $("profilePinyin").textContent = appState.showPinyin ? "已开启" : "已关闭";
    $("profileRecordCount").textContent = `${appState.records.length} 条 →`;
  }

  function currentShelfBooks() {
    return allBooks
      .filter((book) => appState.shelfIds.includes(book.id))
      .map((book) => ({
        ...book,
        category: appState.shelfCategories[book.id] || "未分类",
      }));
  }

  function renderShelfFilters() {
    const filters = ["全部", "睡前故事", "成长启蒙", "自然探索", "亲子共读", "未分类"];
    $("shelfFilters").innerHTML = filters.map((filter) => `
      <button class="chip ${shelfFilter === filter ? "active" : ""}" data-shelf-filter="${filter}">${filter}</button>
    `).join("");
  }

  function renderShelf() {
    const shelfBooks = currentShelfBooks();
    const visible = shelfBooks.filter((book) => shelfFilter === "全部" || book.category === shelfFilter);
    renderShelfFilters();
    $("shelfManageButton").textContent = shelfManage ? "完成" : "管理";
    $("shelfManageButton").hidden = shelfBooks.length === 0;
    $("manageBar").hidden = !shelfManage || shelfBooks.length === 0;
    $("selectedCount").textContent = `已选 ${selectedShelfIds.size} 本`;
    const visibleIds = visible.map((book) => book.id);
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedShelfIds.has(id));
    $("selectAllShelf").textContent = allVisibleSelected ? "取消全选" : "全选";
    $("shelfList").innerHTML = visible.map((book) => `
      <button class="shelf-row ${shelfManage ? "managing" : ""}" data-shelf-book-id="${book.id}">
        ${shelfManage ? `<span class="shelf-check ${selectedShelfIds.has(book.id) ? "checked" : ""}">✓</span>` : ""}
        <img src="${escapeHTML(book.cover || "assets/covers/b002.webp")}" alt="">
        <span class="shelf-copy">
          <strong>${escapeHTML(book.title)}</strong>
          <small>${escapeHTML(book.age)} · ${escapeHTML(book.category)}</small>
          <b>${shelfManage ? "点击选择" : "继续阅读 →"}</b>
        </span>
      </button>
    `).join("");
    $("shelfList").hidden = visible.length === 0;
    $("shelfEmpty").hidden = visible.length > 0;
    if (shelfBooks.length === 0) {
      $("shelfEmptyTitle").textContent = "书架还是空的";
      $("shelfEmptyCopy").textContent = "在绘本详情页点击“加入书架”。";
    } else {
      $("shelfEmptyTitle").textContent = "这个分类还没有书";
      $("shelfEmptyCopy").textContent = "切换到“全部”，或给书架中的书设置分类。";
    }
  }

  function openShelf() {
    shelfManage = false;
    selectedShelfIds.clear();
    shelfFilter = "全部";
    showView("shelf", { tab: "profile" });
  }

  function toggleShelfBook(bookId) {
    if (!shelfManage) {
      openBook(bookId);
      return;
    }
    if (selectedShelfIds.has(bookId)) selectedShelfIds.delete(bookId);
    else selectedShelfIds.add(bookId);
    renderShelf();
  }

  function showSheet(title, options) {
    $("sheetTitle").textContent = title;
    $("sheetOptions").innerHTML = "";
    options.forEach((option) => {
      const button = document.createElement("button");
      button.className = `sheet-option ${option.danger ? "danger" : ""}`;
      button.textContent = option.label;
      button.addEventListener("click", () => {
        closeSheet();
        option.action();
      });
      $("sheetOptions").appendChild(button);
    });
    $("sheetLayer").hidden = false;
  }

  function closeSheet() {
    $("sheetLayer").hidden = true;
  }

  function assignCategory() {
    if (!selectedShelfIds.size) {
      toast("请先选择绘本");
      return;
    }
    showSheet("设置书架分类", SHELF_CATEGORIES.map((category) => ({
      label: category,
      action: () => {
        const categories = { ...appState.shelfCategories };
        selectedShelfIds.forEach((id) => { categories[id] = category; });
        appState.shelfCategories = categories;
        saveState();
        selectedShelfIds.clear();
        renderShelf();
        toast(`已归入${category}`);
      },
    })));
  }

  function removeSelectedBooks() {
    if (!selectedShelfIds.size) {
      toast("请先选择绘本");
      return;
    }
    showSheet(`移出 ${selectedShelfIds.size} 本绘本？`, [
      {
        label: `确认移出 ${selectedShelfIds.size} 本`,
        danger: true,
        action: () => {
          appState.shelfIds = appState.shelfIds.filter((id) => !selectedShelfIds.has(id));
          const categories = { ...appState.shelfCategories };
          selectedShelfIds.forEach((id) => { delete categories[id]; });
          appState.shelfCategories = categories;
          saveState();
          selectedShelfIds.clear();
          renderShelf();
          toast("已移出书架");
        },
      },
    ]);
  }

  function makeTokens(text) {
    return Array.from(text).map((char, index) => ({
      index,
      char,
      pinyin: /[\u3400-\u9fff\u3007]/.test(char) ? (PINYIN[char] || "") : "",
    }));
  }

  function generateDraft() {
    const age = $("originalAge").value;
    const theme = $("originalTheme").value;
    const character = $("originalCharacter").value.trim() || "小狐狸";
    const scene = $("originalScene").value.trim() || "雨后森林";
    const pageTexts = [
      `${character}在${scene}里发现了一道微光。`,
      `${character}决定去看看，它能找到什么呢？`,
      `原来是一群新朋友。${character}勇敢地打了招呼，也收获了${theme}。`,
    ];
    const pages = pageTexts.map((text, index) => ({
      page: index + 1,
      text,
      tokens: makeTokens(text),
      pinyinText: makeTokens(text).map((token) => token.pinyin).filter(Boolean).join(" "),
      scene,
      prompt: index === 0 ? "如果是你，接下来会怎么做？" : "你猜猜看，故事会怎样继续？",
      promptType: index === 0 ? "想象" : "预测",
      image: "assets/covers/b002.webp",
      audio: "",
    }));
    draftBook = {
      id: Date.now(),
      bookNo: `MY-${String(Date.now()).slice(-4)}`,
      title: `${character}找${theme}`,
      age,
      themes: [theme],
      difficulty: "自定义",
      cover: "assets/covers/b002.webp",
      pages,
    };
    $("draftTitle").textContent = draftBook.title;
    $("draftMeta").textContent = `${draftBook.age} · ${theme} · ${pages.length} 页`;
    $("draftPages").innerHTML = pages.map((page) => `
      <div class="draft-page">
        <b>第 ${page.page} 页</b>
        <span>${escapeHTML(page.text)}</span>
      </div>
    `).join("");
    $("draftCard").hidden = false;
    $("draftCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function saveDraft() {
    if (!draftBook) return;
    appState.customBooks = [draftBook, ...appState.customBooks];
    saveState();
    rebuildBooks();
    currentBook = draftBook;
    currentPageIndex = 0;
    draftBook = null;
    $("draftCard").hidden = true;
    renderDetail();
    showView("detail", { tab: "library" });
    toast("原创故事已保存");
  }

  document.querySelectorAll("[data-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.nav;
      if (target === "shelf") openShelf();
      else showView(target, { tab: target });
    });
  });

  document.querySelectorAll("[data-close-sheet]").forEach((button) => {
    button.addEventListener("click", closeSheet);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSheet();
  });

  $("homeBooks").addEventListener("click", (event) => {
    const card = event.target.closest("[data-book-id]");
    if (card) openBook(card.dataset.bookId);
  });

  $("libraryGrid").addEventListener("click", (event) => {
    const card = event.target.closest("[data-book-id]");
    if (card) openBook(card.dataset.bookId);
  });

  $("recordList").addEventListener("click", (event) => {
    const row = event.target.closest("[data-record-book-id]");
    if (row) openBook(row.dataset.recordBookId);
  });

  $("searchInput").addEventListener("input", (event) => {
    libraryQuery = event.target.value;
    renderLibraryResults();
  });

  $("ageFilters").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-age]");
    if (!chip) return;
    activeAge = chip.dataset.age;
    renderLibraryFilters();
    renderLibraryResults();
  });

  $("themeFilters").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-theme]");
    if (!chip) return;
    activeTheme = chip.dataset.theme;
    renderLibraryFilters();
    renderLibraryResults();
  });

  $("detailShelf").addEventListener("click", toggleCurrentShelf);
  $("detailShelfAction").addEventListener("click", toggleCurrentShelf);
  $("detailRead").addEventListener("click", () => startReading(currentBook));
  $("readerBack").addEventListener("click", () => {
    stopAudio();
    renderDetail();
    showView("detail", { tab: "library" });
  });
  $("pinyinToggle").addEventListener("click", togglePinyin);
  $("playButton").addEventListener("click", playCurrentPageAudio);
  $("prevPage").addEventListener("click", goPrevPage);
  $("nextPage").addEventListener("click", goNextPage);
  $("nextPageIcon").addEventListener("click", goNextPage);
  $("readerQuestion").addEventListener("click", () => toast("先看看画面，再请孩子说一说"));
  $("readAgain").addEventListener("click", () => startReading(currentBook));

  $("settingShelf").addEventListener("click", openShelf);
  $("profileShelfButton").addEventListener("click", openShelf);
  $("settingPinyin").addEventListener("click", () => {
    appState.showPinyin = !appState.showPinyin;
    saveState();
    renderProfile();
    toast(appState.showPinyin ? "拼音已显示" : "拼音已隐藏");
  });

  $("shelfManageButton").addEventListener("click", () => {
    shelfManage = !shelfManage;
    if (!shelfManage) selectedShelfIds.clear();
    renderShelf();
  });

  $("shelfFilters").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-shelf-filter]");
    if (!chip) return;
    shelfFilter = chip.dataset.shelfFilter;
    selectedShelfIds.clear();
    renderShelf();
  });

  $("shelfList").addEventListener("click", (event) => {
    const row = event.target.closest("[data-shelf-book-id]");
    if (row) toggleShelfBook(Number(row.dataset.shelfBookId));
  });

  $("selectAllShelf").addEventListener("click", () => {
    const visibleIds = currentShelfBooks()
      .filter((book) => shelfFilter === "全部" || book.category === shelfFilter)
      .map((book) => book.id);
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedShelfIds.has(id));
    if (allSelected) visibleIds.forEach((id) => selectedShelfIds.delete(id));
    else visibleIds.forEach((id) => selectedShelfIds.add(id));
    renderShelf();
  });

  $("assignShelfCategory").addEventListener("click", assignCategory);
  $("removeShelfBooks").addEventListener("click", removeSelectedBooks);
  $("generateDraft").addEventListener("click", generateDraft);
  $("saveDraft").addEventListener("click", saveDraft);

  function applyInitialRoute() {
    const params = new URLSearchParams(window.location.search);
    const requestedView = params.get("view") || "home";
    const requestedBookId = Number(params.get("book"));
    const requestedPage = Math.max(1, Number(params.get("page")) || 1);
    if ((requestedView === "detail" || requestedView === "reader") && requestedBookId) {
      const book = allBooks.find((item) => item.id === requestedBookId);
      if (book) {
        currentBook = book;
        currentPageIndex = Math.min(book.pages.length - 1, requestedPage - 1);
        if (requestedView === "reader") {
          readingStartedAt = Date.now();
          renderReader();
          showView("reader", { tab: "library" });
          return;
        }
        renderDetail();
        showView("detail", { tab: "library" });
        return;
      }
    }
    const validViews = ["home", "library", "original", "growth", "profile", "shelf"];
    if (requestedView === "shelf" && params.get("manage") === "1") {
      shelfManage = true;
    }
    showView(validViews.includes(requestedView) ? requestedView : "home", {
      tab: validViews.includes(requestedView) ? requestedView : "home",
    });
  }

  rebuildBooks();
  renderProfile();
  applyInitialRoute();
})();
