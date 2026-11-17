const config = require("../config");

const COOKIE_KEY = "zhiya_api_cookie";

function baseUrl() {
  return String(config.BASE_URL || "").replace(/\/+$/, "");
}

function storedCookies() {
  return wx.getStorageSync(COOKIE_KEY) || "";
}

function cookieValue(name) {
  const pairs = storedCookies().split(";");
  for (const pair of pairs) {
    const [key, ...rest] = pair.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return "";
}

function mergeCookies(cookies) {
  if (!Array.isArray(cookies) || !cookies.length) return;
  const jar = new Map();
  storedCookies()
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((item) => {
      const [name, ...rest] = item.split("=");
      jar.set(name, rest.join("="));
    });
  cookies.forEach((cookie) => {
    const first = String(cookie).split(";")[0];
    const [name, ...rest] = first.split("=");
    if (name) jar.set(name, rest.join("="));
  });
  wx.setStorageSync(
    COOKIE_KEY,
    Array.from(jar.entries())
      .map(([name, value]) => `${name}=${value}`)
      .join("; "),
  );
}

function request(path, options = {}) {
  if (!config.USE_BACKEND) {
    return Promise.reject(new Error("当前为本地演示模式，未连接后端"));
  }
  const method = options.method || "GET";
  const headers = Object.assign(
    {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    options.header || {},
  );
  const cookie = storedCookies();
  if (cookie) headers.Cookie = cookie;
  const csrf = cookieValue("zhiya_csrf");
  if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl()}${path}`,
      method,
      data: options.data,
      header: headers,
      timeout: config.REQUEST_TIMEOUT,
      success(response) {
        mergeCookies(response.cookies);
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data);
          return;
        }
        const error =
          response.data && response.data.error
            ? response.data.error
            : { code: "HTTP_ERROR", message: `请求失败：${response.statusCode}` };
        const failure = new Error(error.message || "请求失败");
        failure.code = error.code;
        failure.statusCode = response.statusCode;
        failure.payload = response.data;
        reject(failure);
      },
      fail(error) {
        reject(new Error(error.errMsg || "网络请求失败"));
      },
    });
  });
}

function health() {
  return request("/api/v1/health");
}

function register(account, password) {
  return request("/api/v1/auth/registrations", {
    method: "POST",
    data: { account, password },
  });
}

function login(account, password) {
  return request("/api/v1/auth/sessions", {
    method: "POST",
    data: { account, password },
  });
}

function logout() {
  return request("/api/v1/auth/sessions/current", { method: "DELETE" });
}

function listProfiles() {
  return request("/api/v1/child-profiles");
}

function createProfile(profile) {
  return request("/api/v1/child-profiles", {
    method: "POST",
    data: profile,
  });
}

function listStorybooks(params = {}) {
  const query = Object.keys(params)
    .filter((key) => params[key] !== undefined && params[key] !== "")
    .map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(params[key])}`)
    .join("&");
  return request(`/api/v1/storybooks${query ? `?${query}` : ""}`);
}

function createReadingSession(payload) {
  return request("/api/v1/reading-sessions", {
    method: "POST",
    data: payload,
  });
}

function addReadingEvent(sessionId, payload) {
  return request(`/api/v1/reading-sessions/${sessionId}/events`, {
    method: "POST",
    data: payload,
  });
}

function finishReadingSession(sessionId, payload) {
  return request(`/api/v1/reading-sessions/${sessionId}/completions`, {
    method: "POST",
    data: payload,
  });
}

function synthesize(payload) {
  return request("/api/v1/voice/syntheses", {
    method: "POST",
    data: payload,
  });
}

module.exports = {
  health,
  register,
  login,
  logout,
  listProfiles,
  createProfile,
  listStorybooks,
  createReadingSession,
  addReadingEvent,
  finishReadingSession,
  synthesize,
};
