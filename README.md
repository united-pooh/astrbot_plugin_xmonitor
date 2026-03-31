# helloworld

AstrBot 插件模板

A template plugin for AstrBot plugin feature

# 推特监控

这个插件现在会定时调用 TwitterAPI.io 的 `advanced_search` 接口，增量监控指定账号的新推文。

需要配置的项：

- `X-API`: TwitterAPI.io 的 API key
- `TARGET_ACCOUNT`: 要监控的 X/Twitter 账号
- `CHECK_INTERVAL`: 轮询间隔，单位秒，必须是 60 的整数倍，且能整除 3600
- `SUBSCRIBE_GROUPS`: 要主动推送通知的群号列表

当前默认会监控 `Blue_ArchiveJP`，轮询间隔固定为 10 分钟。

当定时任务发现新推文时，插件会主动调用 AstrBot 的 `StarTools.send_message_by_id(...)`，
把通知广播到 `SUBSCRIBE_GROUPS` 里的每个群。

# 支持

- [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
