# 知芽 HTML 版

无需微信开发者工具，直接双击 `index.html` 即可在浏览器中运行。

## 已实现

- 首页主视觉、今日阅读计划、精选绘本
- 绘本馆搜索、年龄筛选、主题筛选
- 绘本详情、收藏与加入书架
- 104 页独立故事插画
- 104 段本地中文朗读音频
- 逐字拼音与朗读同步高亮
- 书架管理模式：多选、全选、分类和平移出
- 阅读记录与成长统计
- 原创故事草稿生成与本地保存
- 阅读进度、书架分类和设置保存在浏览器 `localStorage`

## 直接预览

打开：

`index.html`

也可以使用本地静态服务器：

```powershell
python -m http.server 8080
```

然后访问 `http://localhost:8080`。

## 页面直达参数

- 首页：`index.html`
- 绘本馆：`index.html?view=library`
- 阅读器：`index.html?view=reader&book=2&page=2`
- 书架：`index.html?view=shelf`

## 文件结构

```text
知芽HTML/
├── index.html
├── styles.css
├── app.js
├── preview.png
├── data/
│   ├── books.js
│   └── pinyin-map.js
└── assets/
    ├── audio/
    ├── covers/
    └── pages/
```
