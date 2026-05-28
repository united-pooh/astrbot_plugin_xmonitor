# astrbot_plugin_xmonitor

AstrBot 推特观察者插件。插件会定时调用 TwitterAPI.io 的 `advanced_search` 接口，监控指定 X/Twitter 账号最近 N 分钟内的新推文，并把每条新推文渲染成 Pillow 生成的 base64 图片推送到订阅群。

## 功能

- 使用 `within_time:<CHECK_INTERVAL>m` 查询最近 N 分钟内的推文，减少无效返回。
- 默认查询 `include:nativeretweets`，保留原生转推结果。
- 使用 Pillow 复现 X/Twitter 推文详情布局，不依赖浏览器或 HTML 截图服务。
- 从推文纯文本解析 URL 和 hashtag；URL 会使用 `entities.urls[].display_url` 显示，并渲染为蓝色下划线。
- 支持头像、推文媒体图、卡片图片、时间日期和浏览数渲染。
- 支持在页脚时间/浏览量同一行右侧插入裁边后的来源 logo，默认使用 `assets/source_logo.png`。
- 自动把获取到的原始推文保存到本地 sqlite 历史库，并使用原文 SHA-256 前 6 位作为短 ID。
- 自动缓存 `TARGET_ACCOUNT` 的头像图片为 base64，避免历史渲染依赖可能失效的远程头像 URL。
- 支持 `/history` 查看最近 10 条历史推文，支持 `/x <短ID>` 重新渲染历史原推文图片。
- 支持 `/x <短ID> <翻译正文>` 使用历史原推文素材渲染翻译正文图片，翻译正文可包含链接、换行、hashtag 和 emoji。
- 默认通过 `MessageChain().base64_image(...)` 发送图片；渲染或发送失败时自动回退纯文本通知。

## 配置

- `X-API`: TwitterAPI.io 的 API key。
- `TARGET_ACCOUNT`: 要监控的 X/Twitter 账号，默认 `Blue_ArchiveJP`。
- `CHECK_INTERVAL`: 轮询间隔和 `within_time` 查询窗口，单位分钟，默认 `10`，必须大于 0。
- `SUBSCRIBE_GROUPS`: 要主动推送通知的群号列表。
- `NOTIFY_USER`: 广播时要 `@` 的用户 QQ，不填则不 `@`。
- `SOURCE_LOGO`: 正文下方的一行来源 logo 图片路径；相对路径按插件目录解析，留空则不显示。
- `AUTO_DOWNLOAD_FONTS`: 是否在插件初始化时后台下载 Noto Sans CJK 字体到 `data/fonts`，默认开启；下载任务不会阻塞 `initialize()`。
- `FONT_PATHS`: 自定义常规字体路径列表。Docker 容器无法访问系统 CJK 字体时，可以挂载字体文件后在这里指定路径。
- `BOLD_FONT_PATHS`: 自定义粗体字体路径列表；不填时会回退到 `FONT_PATHS` 或默认字体。
- `EMOJI_FONT_PATHS`: 自定义 emoji 字体路径列表。Docker 容器没有彩色 emoji 字体时，可以挂载 `NotoColorEmoji.ttf` 或 Twemoji 字体后在这里指定路径。

Docker 容器中如果没有 CJK 字体，Pillow 会把中文/日文渲染成方框；如果没有彩色 emoji 字体，emoji 也可能无法正确渲染。插件会优先使用配置的字体路径，其次使用自动下载到 `data/fonts` 的 Noto Sans CJK 和 Noto Color Emoji，再尝试常见系统字体路径；如果仍找不到可用正文字体，会返回明确错误而不是继续生成全方框图片。

## 依赖

安装插件依赖：

```bash
pip install -r requirements.txt
```

依赖包括 `httpx`、`APScheduler`、`pytz` 和 `Pillow`。

## 行为说明

定时器使用 APScheduler 的 `interval` 触发器，从插件启动时开始计时。每次查询都会请求 TwitterAPI.io 的 `advanced_search`：

```text
from:<TARGET_ACCOUNT> include:nativeretweets within_time:<CHECK_INTERVAL>m
```

每次拉取新推文前，插件会先检查 `data/tweet_history.sqlite3` 中是否已有 `TARGET_ACCOUNT` 的头像缓存；如果没有，会请求 TwitterAPI.io 的 `/twitter/user/info?userName=<TARGET_ACCOUNT>`，读取 `profilePicture`，下载头像并以 base64 写入本地数据库。后续渲染会优先使用这个头像缓存，避免历史图片依赖远程头像 URL。

如果发现新推文，插件会先把原始正文和原始 JSON 写入 `data/tweet_history.sqlite3`，再为每条推文生成一张 PNG，并以 base64 图片消息发送到 `SUBSCRIBE_GROUPS` 中的每个群。历史短 ID 来自原始正文的 SHA-256 前 6 位；同一原始正文重复出现时不会重复入库。

`SOURCE_LOGO` 图片会自动裁掉透明或纯色边缘，再按页脚一行高度缩放，放在时间/浏览量同一行右侧并与媒体右边框对齐。如果远程头像、媒体图或卡片图加载失败，渲染器会降级处理；如果整张图片渲染或图片消息发送失败，插件会发送纯文本 fallback。

## 命令

- `/new` 或 `/newx`: 手动拉取最近 `<CHECK_INTERVAL>` 分钟内的新推文，并写入历史库。
- `/history`: 查看最近保存的 10 条历史推文，从新到旧显示，每条包含 `#短ID`、时间和正文摘要。
- `/x <短ID>`: 按短 ID 查找历史推文，并重新渲染原推文图片。
- `/x <短ID> <翻译正文>`: 按短 ID 查找历史推文，复用原推文头像、媒体图、卡片图等素材，把正文替换为输入的翻译正文后渲染图片。

示例：

```text
/x 114514 翻译正文 https://example.com/news
#测试 😀
```

## 验证

```bash
python -m unittest discover -s tests
python -m pytest -q
python -m py_compile main.py tweet_renderer.py scheduler.py history_store.py
python -m mypy .
python -m ruff check .
git diff --check
```
