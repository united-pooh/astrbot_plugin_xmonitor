# helloworld

AstrBot 插件模板

A template plugin for AstrBot plugin feature

# 推特监控

这个插件现在会定时调用 TwitterAPI.io 的 `advanced_search` 接口，增量监控指定账号的新推文。

需要配置的项：

- `X-API`: TwitterAPI.io 的 API key
- `TARGET_ACCOUNT`: 要监控的账号，默认 `elonmusk`
- `CHECK_INTERVAL`: 检查间隔，单位秒，默认 `300`

# 支持

- [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
