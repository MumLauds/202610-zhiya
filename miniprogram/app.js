const config = require("./config");

App({
  globalData: {
    config,
    apiStatus: config.USE_BACKEND ? "准备连接后端" : "本地演示模式",
  },

  onLaunch() {
    if (!config.USE_BACKEND) return;
    const api = require("./services/api");
    api
      .health()
      .then(() => {
        this.globalData.apiStatus = "后端服务正常";
      })
      .catch(() => {
        this.globalData.apiStatus = "后端服务未连接";
      });
  },
});
