# 离线英文阅读与学习平台 · 每日 TIME

每天自动抓取 **TIME** 杂志 `健康 / 科技 / 商业` 三板块各 3 篇最新文章，**全文+图片本地化**，在浏览器里离线阅读、划词高亮、写批注、建生词本，帮你养成一天一篇的英文阅读习惯。

![python](https://img.shields.io/badge/Python-3.9%2B-blue)
![flask](https://img.shields.io/badge/Flask-3.x-000)
![sqlite](https://img.shields.io/badge/SQLite-WAL-003B57)
![license](https://img.shields.io/badge/license-MIT-green)

## 功能总览

### 日更抓取
- 每日 08:00 (Asia/Shanghai) 自动抓取 Health / Tech / Business 各 3 篇
- 用 `readability-lxml` + BeautifulSoup 提取正文，去广告/推荐/脚注
- 正文图片全部下载到 `data/assets/<id>/`，`<img src>` 重写为本地路径
- 一键手动刷新

### 离线阅读
- 每篇文章都有专属的阅读器页面 `/articles/<id>`
- 文本、封面、文内图片全部从本地服务读取
- 详情页拦截所有外链点击，不做任何外部跳转

### 我的文章库
- ⭐ 收藏 · 📚 加入每日文章库 · ✓ 标记已读
- 按全部 / 收藏 / 库中 / 未读 / 已读 筛选
- 支持分类筛选 + 全文搜索（标题、摘要、正文）

### 划词学习
- 阅读器中划选任意文本，弹出浮动工具条
- 四色高亮 / 添加英文批注 / 一键加入生词本
- 锚点用 DOM Range 序列化（XPath + offset），丢失时用 `prefix/quote/suffix` 模糊恢复

### 生词本
- 词形还原后自动去重（复数/时态/比较级/常见后缀）
- 保存上下文、来源文章、记忆点
- 4 级掌握度（新词 / 学习中 / 熟悉 / 已掌握）
- 一键导出 CSV（带 BOM，Excel 直接打开，Anki 可导入）

## 项目结构

```
foreign magazine grab daily/
├── app.py                 # Flask 页面 + REST API + 调度器
├── scraper.py             # TIME 抓取 + 提取 + 图片本地化
├── extractor.py           # readability + HTML 清洗
├── annotations.py         # 词形还原（单词/短语）
├── db.py                  # SQLite: articles / annotations / vocabulary
├── requirements.txt
├── run_daily.sh           # 可选 cron 脚本
├── data/
│   ├── app.db             # SQLite（自动生成，WAL 模式）
│   └── assets/<id>/*.jpg  # 本地文章图片
├── templates/
│   ├── index.html         # 今日 3×3 概览
│   ├── library.html       # 我的文章库
│   ├── reader.html        # 阅读器 + 划词面板
│   └── vocabulary.html    # 生词本
├── static/
│   ├── style.css
│   ├── app.js             # 首页
│   ├── library.js
│   ├── reader.js          # 划词、锚点序列化、面板、弹窗
│   └── vocab.js
└── logs/
    ├── scraper.log
    └── cron.log
```

## 快速开始

```bash
cd "foreign magazine grab daily"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py               # 启动服务
# 首次启动会在后台抓取当天文章；几秒后刷新页面即可看到。
```

浏览器打开 <http://localhost:5050>，顶部导航切换 **Today / Library / Vocabulary**。

### 手动抓取一次
```bash
python3 scraper.py
```
完成后 `data/app.db` 被更新，`data/assets/<id>/` 里写入本地图片。

## 每日自动抓取

### 方式 A：服务内置调度（推荐）
`python3 app.py` 常驻即可，APScheduler 会在每天 08:00 (Asia/Shanghai) 自动跑一次。

### 方式 B：系统 cron
```bash
crontab -e
0 8 * * * /绝对路径/foreign\ magazine\ grab\ daily/run_daily.sh
```

## 开机自启 + 挂掉自动重启（推荐）

### macOS（launchd）
仓库已提供模板文件：`deploy/launchd/com.foreignmagazine.daily.plist`。

1) 先把其中的 `WorkingDirectory` 与 `ProgramArguments` 里的路径改成你机器上的真实项目路径（当前模板默认是：
`/Users/BGS/Documents/foreign magazine grab daily`）。

2) 加载并设为开机自启：

```bash
mkdir -p ~/Library/LaunchAgents
cp "deploy/launchd/com.foreignmagazine.daily.plist" ~/Library/LaunchAgents/
launchctl unload -w ~/Library/LaunchAgents/com.foreignmagazine.daily.plist 2>/dev/null || true
launchctl load -w ~/Library/LaunchAgents/com.foreignmagazine.daily.plist
```

3) 查看状态与日志：

```bash
launchctl list | grep com.foreignmagazine.daily || true
tail -n 200 "logs/launchd.out.log"
tail -n 200 "logs/launchd.err.log"
```

`KeepAlive=true` 会在进程退出时自动拉起，实现“挂掉就重启”。

### Linux（systemd）
仓库提供模板：`deploy/systemd/foreign-magazine-grab-daily.service`（你需要把 `WorkingDirectory` 改成实际路径，例如 `/opt/foreign-magazine-grab-daily`）。

```bash
sudo cp deploy/systemd/foreign-magazine-grab-daily.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now foreign-magazine-grab-daily
sudo systemctl status foreign-magazine-grab-daily --no-pager
```

### 手动刷新
UI 右上角 **立即刷新**，或：
```bash
curl -X POST http://localhost:5050/api/refresh
```

## 阅读器：一键同步单词到墨墨

在阅读器页面（`/articles/<id>`）选中单词/词组后，工具条里新增了 **「＋ 墨墨」**，也可在“加入生词本”弹窗中点 **「同步到墨墨」**。

### 配置
在墨墨 App 中获取开放 API Token 后，启动服务前设置环境变量：

```bash
export MAIMEMO_TOKEN="BearerTokenHere"
# 或者（兼容你可能已有的变量名）
export MOMO_API_KEY="BearerTokenHere"
python3 app.py
```

后端会按以下流程调用墨墨开放 API：
1) `GET https://open.maimemo.com/open/api/v1/vocabulary?spelling=<word>` 查询词条并拿到 `id`
2) `POST https://open.maimemo.com/open/api/v1/study/add_words` 把 `id` 加入墨墨学习词库

## REST API（供脚本/插件使用）

| Method | Path | Notes |
|--------|------|-------|
| GET    | `/api/dates` | 已抓取日期列表 |
| GET    | `/api/categories` | 板块元数据 |
| GET    | `/api/feed?date=YYYY-MM-DD` | 今日 3×3 feed |
| GET    | `/api/library?filter=all\|favorite\|library\|read\|unread&category=&q=` | 文章库 |
| GET    | `/api/articles/<id>` | 单篇详情（含 `content_html`） |
| POST   | `/api/articles/<id>/favorite` | `{"value": true}`；无 body 则翻转 |
| POST   | `/api/articles/<id>/library` | 加入/移出每日库 |
| POST   | `/api/articles/<id>/read` | 标记已读/未读 |
| GET/POST | `/api/articles/<id>/annotations` | 列表 / 新增高亮/批注 |
| PATCH/DELETE | `/api/annotations/<id>` | 修改 / 删除 |
| GET/POST | `/api/vocabulary?q=&mastery=&sort=recent\|alpha\|mastery` | 列表 / 新增 |
| PATCH/DELETE | `/api/vocabulary/<id>` | 修改（掌握度、笔记） / 删除 |
| GET    | `/api/vocabulary/export.csv` | 导出 CSV |
| POST   | `/api/refresh` | 立即抓取 |

### 划词 annotation 锚点格式
```json
{
  "kind": "highlight" | "note",
  "color": "yellow" | "pink" | "green" | "blue",
  "quote": "选中的原文",
  "prefix": "前32字符",
  "suffix": "后32字符",
  "start_xpath": "p[2]/text()[1]",
  "start_offset": 12,
  "end_xpath":   "p[2]/text()[1]",
  "end_offset":  45,
  "comment": "可选个人批注"
}
```
XPath 相对于 `#reader-body`。恢复时先按 XPath 精确定位，失败则用 `prefix+quote+suffix` 在纯文本里模糊查找。

## 自定义

- **板块**：`scraper.py` 的 `CATEGORIES` 字典
- **每板块条数**：`scraper.run(limit_per_category=5)`
- **调度时间**：`app.py` 的 `_start_scheduler()`
- **端口**：`PORT=5055 python3 app.py`
- **样式**：`static/style.css`（色板在 `:root`）

## 常见问题

**Q：抓取失败/返回 HTTP 406？**
TIME 对无 `Accept` 头的请求会拒绝；脚本已设置好，若仍失败请检查网络。

**Q：图片没下载下来？**
日志里能看到 `image download failed`。通常是国外图床临时抖动，下次抓取会自动补齐。

**Q：高亮位置失配？**
第一保险是 XPath+offset；如果 HTML 小幅变化，会退回到 `prefix/quote/suffix` 模糊匹配；都失败时只是不渲染这条高亮，不会丢数据。

**Q：想同步到手机？**
单机本地版本不带同步；但你随时可以 `scp data/app.db` 到任意机器，也能把 `data/assets/` 一起拷走继续离线阅读。

## License
MIT
