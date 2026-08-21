# B 端交付清单

交付版本：Companion Profile Engine API v0.2.0  
交付日期：2026-08-02

## 本压缩包内容

| 文件 | B 端用途 |
| --- | --- |
| `README.md` | 接入流程、鉴权、接口、重试和验收标准 |
| `openapi.json` | 导入 API 平台或生成客户端类型 |
| `companion-profile-engine.postman_collection.json` | 直接联调接口 |
| `client.env.example` | B 端服务配置项模板 |
| `quickstart.mjs` | Node.js 18+ 服务端调用示例 |

## 随压缩包提供的信息

发送压缩包时，在交付消息中同时写明：

```text
测试环境 BASE_URL：<待部署负责人填写>
生产环境 BASE_URL：<待部署负责人填写>
测试环境 TENANT_ID：<待画像引擎负责人填写>
生产环境 TENANT_ID：<待画像引擎负责人填写>
联调联系人：<待填写>
```

`API_KEY` 不写进压缩包或普通消息。测试和生产 Key 分别通过密码管理器等安全渠道发送给 B 端指定服务端负责人。

## B 端回执标准

B 端完成以下结果即可确认接入：

1. `/health` 返回 `status=ok` 且数据库为 `ok`。
2. 新用户能够完成初始化和读取画像。
3. 一轮用户消息能够成功摄取并获得 `reply_hints`。
4. 相同幂等键和相同请求体重复提交时返回相同结果。
5. 过期画像版本返回 HTTP 409，错误 Key 返回 HTTP 401。

注意：只能由 B 端服务端调用，不能把 `API_KEY` 放进网页、桌面端或移动端代码。
