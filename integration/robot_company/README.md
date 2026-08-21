# Companion Profile Engine｜机器人公司 API 接入包

请先阅读 `API_GUIDE.md`。正式联调需要我方另行提供：

```text
BASE_URL
TENANT_ID
API_KEY（安全渠道单独发送）
```

文件说明：

- `API_GUIDE.md`：给机器人公司开发人员的简明使用手册。
- `API_ACCEPTANCE_REPORT.md`：当前版本实测结果、上线条件和未验证边界。
- `openapi.json`：机器可读 API 契约。
- `postman_collection.json`：Postman 联调集合。
- `client.env.example`：服务端环境变量模板。
- `examples/quickstart.mjs`：Node.js 18+ 最小接入示例。

只能由机器人公司的服务端调用，不能在网页、机器人客户端或移动 App 中保存 `API_KEY`。
