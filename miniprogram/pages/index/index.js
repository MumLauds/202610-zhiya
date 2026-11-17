const seed = require("../../data/books");
const config = require("../../config");
const store = require("../../utils/store");
const api = require("../../services/api");

const AGE_OPTIONS = [
  { label: "全部", value: "all" },
  { label: "3–4 岁", value: "3–4 岁" },
  { label: "4–5 岁", value: "4–5 岁" },
  { label: "5–6 岁", value: "5–6 岁" },
];

const THEME_OPTIONS = [
  "亲子依恋",
  "勇气",
  "友谊",
  "自然观察",
  "情绪安抚",
  "想象",
  "分享",
  "生命成长",
  "合作",
  "习惯",
];

function ageLabel(ageBand) {
  return (
    {
      AGE_3_4: "3–4 岁",
      AGE_4_5: "4–5 岁",
      AGE_5_6: "5–6 岁",
    }[ageBand] || "3–4 岁"
  );
}

function dateLabel(timestamp) {
  const date = new Date(timestamp);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )}`;
}

function decorateBook(book) {
  return Object.assign({}, book, {
    themeText: book.themes.join(" · "),
    pageCount: book.pages.length,
    firstText: book.pages.length ? book.pages[0].text : "",
  });
}

Page({
  data: {
    view: "home",
    activeTab: "home",
    showTabBar: true,
    books: [],
    filteredBooks: [],
    homeBooks: [],
    shelfBooks: [],
    homeHero: null,
    query: "",
    ageOptions: AGE_OPTIONS,
    activeAge: "all",
    themeOptions: THEME_OPTIONS,
    activeTheme: "",
    currentBook: null,
    currentPageIndex: 0,
    currentPage: null,
    pageProgress: "",
    showPinyin: true,
    spokenIndex: -1,
    speechState: "idle",
    inShelf: false,
    child: null,
    childAgeLabel: "",
    records: [],
    recordStats: {
      count: 0,
      minutes: 0,
      books: 0,
      days: 0,
    },
    originalForm: {
      age: "3–4 岁",
      theme: "勇气",
      character: "小狐狸",
      scene: "雨后森林",
    },
    draft: null,
    loginOpen: false,
    loginAccount: "",
    loginPassword: "",
    backendEnabled: Boolean(config.USE_BACKEND),
    apiStatus: config.USE_BACKEND ? "准备连接后端" : "本地演示模式",
    summary: null,
    statusBarHeight: 44,
  },

  onLoad() {
    const systemInfo =
      typeof wx.getWindowInfo === "function"
        ? wx.getWindowInfo()
        : wx.getSystemInfoSync();
    this._state = store.read();
    this._allBooks = seed.books
      .concat(this._state.customBooks || [])
      .map(decorateBook);
    this._readingStartedAt = null;
    this._visitedPages = new Set();
    this._speechTimer = null;

    const homeBooks = this._allBooks.slice(0, 6);
    this.setData({
      books: this._allBooks,
      filteredBooks: this._allBooks,
      homeBooks,
      homeHero: this._allBooks.find((book) => book.id === 2) || homeBooks[0],
      child: this._state.child,
      childAgeLabel: ageLabel(this._state.child.ageBand),
      showPinyin: this._state.settings.showPinyin,
      statusBarHeight: systemInfo.statusBarHeight || 44,
    });
    this.syncStoredData();
    if (config.USE_BACKEND) {
      api
        .health()
        .then(() => this.setData({ apiStatus: "后端服务正常" }))
        .catch(() => this.setData({ apiStatus: "后端服务未连接" }));
    }
  },

  onUnload() {
    this.clearSpeech();
  },

  onShareAppMessage() {
    return {
      title: "知芽亲子绘本共读",
      path: "/pages/index/index",
    };
  },

  syncStoredData() {
    const records = this._state.records || [];
    const shelfBooks = this._allBooks.filter((book) =>
      this._state.shelfIds.includes(book.id),
    );
    const totalMinutes = records.reduce(
      (sum, item) => sum + Number(item.minutes || 0),
      0,
    );
    const uniqueBooks = new Set(records.map((item) => item.bookId)).size;
    const uniqueDays = new Set(records.map((item) => item.date)).size;
    this.setData({
      shelfBooks,
      records,
      recordStats: {
        count: records.length,
        minutes: totalMinutes,
        books: uniqueBooks,
        days: uniqueDays,
      },
    });
  },

  showView(view, tab, showTabBar) {
    this.clearSpeech();
    this.setData({
      view,
      activeTab: tab || this.data.activeTab,
      showTabBar:
        typeof showTabBar === "boolean" ? showTabBar : view === "home",
    });
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  switchTab(event) {
    const tab = event.currentTarget.dataset.tab;
    this.showView(tab, tab, true);
  },

  openLibrary() {
    this.showView("library", "library", true);
  },

  openProfile() {
    this.showView("profile", "profile", true);
  },

  noop() {},

  goHome() {
    this.showView("home", "home", true);
  },

  goBack() {
    const view = this.data.view;
    if (view === "reader") {
      this.showView("detail", "library", false);
      return;
    }
    if (view === "originalDraft") {
      this.showView("original", "original", true);
      return;
    }
    if (view === "shelf") {
      this.showView("profile", "profile", true);
      return;
    }
    if (view === "summary" || view === "detail") {
      this.showView("library", "library", true);
      return;
    }
    this.goHome();
  },

  onSearchInput(event) {
    this.setData({ query: event.detail.value });
    this.filterLibrary();
  },

  clearSearch() {
    this.setData({ query: "" });
    this.filterLibrary();
  },

  selectAge(event) {
    this.setData({ activeAge: event.currentTarget.dataset.age });
    this.filterLibrary();
  },

  selectTheme(event) {
    const value = event.currentTarget.dataset.theme;
    this.setData({ activeTheme: this.data.activeTheme === value ? "" : value });
    this.filterLibrary();
  },

  filterLibrary() {
    const query = String(this.data.query || "").trim().toLowerCase();
    const filteredBooks = this._allBooks.filter((book) => {
      const ageMatch =
        this.data.activeAge === "all" || book.age === this.data.activeAge;
      const themeMatch =
        !this.data.activeTheme || book.themes.includes(this.data.activeTheme);
      const text = `${book.title} ${book.bookNo} ${book.themes.join(" ")}`.toLowerCase();
      return ageMatch && themeMatch && (!query || text.includes(query));
    });
    this.setData({ filteredBooks });
  },

  openBook(event) {
    const id = Number(event.currentTarget.dataset.id);
    const book = this._allBooks.find((item) => item.id === id);
    if (!book) return;
    this.setData({
      currentBook: book,
      inShelf: this._state.shelfIds.includes(book.id),
    });
    this.showView("detail", "library", false);
  },

  openShelf() {
    this.showView("shelf", "profile", false);
  },

  toggleShelf() {
    const book = this.data.currentBook;
    if (!book) return;
    const ids = new Set(this._state.shelfIds);
    if (ids.has(book.id)) {
      ids.delete(book.id);
      wx.showToast({ title: "已移出书架", icon: "none" });
    } else {
      ids.add(book.id);
      wx.showToast({ title: "已加入书架", icon: "success" });
    }
    this._state = store.update({ shelfIds: Array.from(ids) });
    this.setData({ inShelf: ids.has(book.id) });
    this.syncStoredData();
  },

  startReading() {
    if (!this.data.currentBook) return;
    this._readingStartedAt = Date.now();
    this._visitedPages = new Set([0]);
    this.setPage(0);
    this.showView("reader", "library", false);
  },

  setPage(index) {
    const book = this.data.currentBook;
    if (!book) return;
    const page = book.pages[index];
    if (!page) return;
    this._visitedPages.add(index);
    this.setData({
      currentPageIndex: index,
      currentPage: page,
      pageProgress: `${index + 1} / ${book.pages.length}`,
      spokenIndex: -1,
    });
  },

  prevPage() {
    if (this.data.currentPageIndex <= 0) {
      this.goBack();
      return;
    }
    this.clearSpeech();
    this.setPage(this.data.currentPageIndex - 1);
  },

  nextPage() {
    const book = this.data.currentBook;
    if (!book) return;
    this.clearSpeech();
    if (this.data.currentPageIndex >= book.pages.length - 1) {
      this.finishReading();
      return;
    }
    this.setPage(this.data.currentPageIndex + 1);
  },

  finishReading() {
    const book = this.data.currentBook;
    const startedAt = this._readingStartedAt || Date.now();
    const minutes = Math.max(
      1,
      Math.round((Date.now() - startedAt) / 60000),
    );
    const record = {
      id: `record-${Date.now()}`,
      bookId: book.id,
      title: book.title,
      cover: book.cover,
      minutes,
      pages: Math.max(1, this._visitedPages.size || book.pages.length),
      date: dateLabel(Date.now()),
      timestamp: Date.now(),
    };
    const records = [record].concat(this._state.records || []).slice(0, 50);
    this._state = store.update({ records });
    this.syncStoredData();
    this.setData({
      summary: {
        title: book.title,
        minutes,
        pages: record.pages,
        interactions: 2,
      },
    });
    this.showView("summary", "growth", false);
  },

  togglePinyin() {
    const showPinyin = !this.data.showPinyin;
    this._state = store.update({
      settings: Object.assign({}, this._state.settings, { showPinyin }),
    });
    this.setData({ showPinyin });
  },

  clearSpeech() {
    if (this._speechTimer) {
      clearInterval(this._speechTimer);
      this._speechTimer = null;
    }
    if (
      this.data.speechState !== "idle" ||
      this.data.spokenIndex !== -1
    ) {
      this.setData({ speechState: "idle", spokenIndex: -1 });
    }
  },

  readAloud() {
    if (this.data.speechState === "playing") {
      this.clearSpeech();
      return;
    }
    const page = this.data.currentPage;
    if (!page || !page.tokens || !page.tokens.length) return;
    let index = 0;
    this.setData({ speechState: "playing", spokenIndex: 0 });
    this._speechTimer = setInterval(() => {
      index += 1;
      if (index >= page.tokens.length) {
        this.clearSpeech();
        return;
      }
      this.setData({ spokenIndex: index });
    }, 320);
    if (config.USE_BACKEND) {
      wx.showToast({
        title: "朗读高亮已开启；TTS需后端会话与语音密钥",
        icon: "none",
      });
    } else {
      wx.showToast({
        title: "本地演示朗读：仅演示逐字高亮",
        icon: "none",
      });
    }
  },

  openOriginal() {
    this.showView("original", "original", true);
  },

  onOriginalInput(event) {
    const field = event.currentTarget.dataset.field;
    const form = Object.assign({}, this.data.originalForm);
    form[field] = event.detail.value;
    this.setData({ originalForm: form });
  },

  selectOriginalAge(event) {
    const form = Object.assign({}, this.data.originalForm, {
      age: event.currentTarget.dataset.age,
    });
    this.setData({ originalForm: form });
  },

  selectOriginalTheme(event) {
    const form = Object.assign({}, this.data.originalForm, {
      theme: event.currentTarget.dataset.theme,
    });
    this.setData({ originalForm: form });
  },

  generateDraft() {
    const form = this.data.originalForm;
    const pages = [
      {
        page: 1,
        text: `${form.character}在${form.scene}里发现了一道微光。`,
      },
      {
        page: 2,
        text: `${form.character}决定去看看，它能找到什么呢？`,
      },
      {
        page: 3,
        text: `原来是一群新朋友。${form.character}勇敢地打了招呼，也收获了${form.theme}。`,
      },
    ].map((page) => ({
      page: page.page,
      text: page.text,
      pinyinText: "本地生成内容待补充拼音",
      tokens: Array.from(page.text).map((char, index) => ({
        index,
        char,
        pinyin: "",
      })),
      scene: form.scene,
      prompt: "如果是你，接下来会怎么做？",
      promptType: "想象",
    }));
    this.setData({
      draft: {
        id: Date.now(),
        title: `${form.character}找${form.theme}`,
        age: form.age,
        themes: [form.theme],
        cover: this.data.homeHero.cover,
        pages,
      },
    });
    this.showView("originalDraft", "original", false);
  },

  saveDraft() {
    const draft = this.data.draft;
    if (!draft) return;
    const customBook = decorateBook({
      id: draft.id,
      bookNo: `MY-${String(draft.id).slice(-4)}`,
      title: draft.title,
      age: draft.age,
      themes: draft.themes,
      difficulty: "自定义",
      cover: draft.cover,
      pages: draft.pages,
    });
    const customBooks = [customBook].concat(this._state.customBooks || []);
    this._state = store.update({ customBooks });
    this._allBooks = [customBook].concat(this._allBooks);
    this.setData({
      books: this._allBooks,
      filteredBooks: this._allBooks,
      currentBook: customBook,
      inShelf: false,
      draft: null,
    });
    wx.showToast({ title: "原创故事已保存", icon: "success" });
    this.showView("detail", "original", false);
  },

  changeChildAge() {
    const values = ["3–4 岁", "4–5 岁", "5–6 岁"];
    wx.showActionSheet({
      itemList: values,
      success: (result) => {
        const age = values[result.tapIndex];
        const ageBand = ["AGE_3_4", "AGE_4_5", "AGE_5_6"][result.tapIndex];
        const child = Object.assign({}, this._state.child, { ageBand });
        this._state = store.update({ child });
        this.setData({ child, childAgeLabel: age });
      },
    });
  },

  openLogin() {
    this.setData({ loginOpen: true });
  },

  closeLogin() {
    this.setData({ loginOpen: false });
  },

  onLoginInput(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  submitLogin() {
    if (!config.USE_BACKEND) {
      wx.showModal({
        title: "本地演示模式",
        content:
          "请先修改 config.js：USE_BACKEND 设为 true，并填写 BASE_URL 和合法请求域名。",
        showCancel: false,
      });
      return;
    }
    api
      .login(this.data.loginAccount, this.data.loginPassword)
      .then(() => {
        wx.showToast({ title: "登录成功", icon: "success" });
        this.setData({ loginOpen: false, apiStatus: "已登录后端" });
      })
      .catch((error) => {
        wx.showToast({ title: error.message || "登录失败", icon: "none" });
      });
  },
});
