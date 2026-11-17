# 知芽微信小程序

## 导入方式

1. 打开微信开发者工具。
2. 选择“导入项目”。
3. 项目目录选择本目录。
4. AppID 使用代码中的 `touristappid`，或在 `project.config.json` 中替换为团队小程序 AppID。
5. 本地演示默认不连接后端，直接编译即可运行。

## 已实现页面

- 首页：主视觉、今日阅读计划、精选绘本
- 绘本馆：搜索、年龄筛选、主题筛选
- 绘本详情：封面、适龄、主题、共读入口、加入书架
- 亲子共读：逐页翻页、逐字拼音、页面进度、共读问题、朗读高亮
- 阅读小结：阅读时长、页数、互动次数
- 原创故事：家长设置角色和场景，生成并保存家庭绘本草稿
- 成长记录：本地保存阅读历史
- 家长中心：孩子档案、书架、拼音设置、后端账号入口
- 我的书架：收藏绘本与继续阅读

## 数据

- 20 本仓库原创绘本
- 104 个真实页面文本
- 逐字拼音
- 19 张压缩后的真实 PNG 封面

数据来自仓库中的 `data/seed/story_pages.json`、`story_versions.json` 和 `knowledge-base_v2.5/图片/`。

## 后端接入

默认 `config.js`：

```js
module.exports = {
  USE_BACKEND: false,
  BASE_URL: "http://127.0.0.1:8000",
};
```

切换为真实后端时，将 `USE_BACKEND` 改为 `true`，填写公网 HTTPS 服务地址，并在微信公众平台配置 `request` 合法域名。

`services/api.js` 已提供登录、儿童档案、绘本列表、阅读会话、阅读事件和阅读小结接口封装。

## 朗读说明

当前小程序在没有后端语音服务时使用逐字高亮模拟朗读。正式语音需要：

- 后端配置百炼 TTS；
- 阅读会话与页面上下文存在；
- 微信小程序配置合法 HTTPS 请求域名；
- 使用 `services/api.js` 中的语音合成接口返回音频。

## 目录

```text
miniprogram/
├── app.js
├── app.json
├── app.wxss
├── config.js
├── services/api.js
├── utils/store.js
├── data/books.js
├── assets/covers/
├── assets/icons/
└── pages/index/
```
