# 系统使用与交付说明

## 交付入口

| 项目 | 地址 |
| --- | --- |
| 网站工作台 | `https://companion-profile-engine-6a60.zeabur.app/demo` |
| Swagger API | `https://companion-profile-engine-6a60.zeabur.app/docs` |
| 健康检查 | `https://companion-profile-engine-6a60.zeabur.app/health` |
| API Base URL | `https://companion-profile-engine-6a60.zeabur.app` |

网站访问码、`X-Tenant-ID` 与 `X-API-Key` 必须由项目负责人通过安全渠道单独提供，不写入仓库、文档或 Postman Collection。

## 系统配置

- 服务版本：`0.5.0`；
- API 版本：`v1`；
- 生产存储：Zeabur PostgreSQL；
- 默认模型选项：DeepSeek V3.2；
- 可选模型参数：`deepseek`、`claude`、`gpt`、`glm`、`gemini`、`kimi`；
- 所有模型均通过 OpenRouter 兼容接口封装，浏览器不持有模型密钥；
- 当前 Zeabur 香港部署调用 Claude 可能返回上游区域限制错误，选项与 API 契约仍保留。

## API 鉴权

业务接口必须携带：

```http
X-Tenant-ID: <tenant-id>
X-API-Key: <api-key>
```

所有 POST 写入接口还必须携带唯一的：

```http
Idempotency-Key: <request-id>
```

## API 快速检查流程

1. `GET /health`：应用和数据库均为 `ok`；
2. `GET /v1/capabilities`：确认版本、规则包、模型选项和限流；
3. `POST /v1/profiles:init`：创建测试画像；
4. `POST /v1/profiles/{user_id}/messages:ingest`：传入 `model_provider=deepseek`；
5. `GET /v1/profiles/{user_id}`：确认画像版本和证据更新；
6. 使用相同 `Idempotency-Key` 重放：结果相同且版本不重复增长；
7. 使用旧 `expected_profile_version` 写入：返回 `409 profile_version_conflict`；
8. `POST /v1/profiles/{user_id}:forget`：使用 `scope=all_profile` 清理测试数据。

完整请求体、JavaScript/Python 示例见 [API 使用文档](API_USAGE.md)。可直接导入 [Postman Collection](../postman/companion-profile-engine.postman_collection.json)。

## 模型选择

消息摄取请求中的模型选择字段：

```json
{
  "model_provider": "deepseek"
}
```

允许值为 `deepseek`、`claude`、`gpt`、`glm`、`gemini` 或 `kimi`。未传时使用服务器默认抽取策略。网站顶部的模型下拉框使用同一字段，不会向浏览器返回 OpenRouter Key。

## 功能检查标准

- 网站、Swagger、健康检查和鉴权 API 可访问；
- DeepSeek 请求返回 `200`，且 `semantic_extractor_version` 包含 `deepseek/deepseek-v3.2`；
- 画像版本、幂等写入、版本冲突、规则包读取和遗忘清理符合预期；
- 响应包含 `X-Request-ID`、`X-API-Version`、限流和安全 Header；
- 数据库或模型异常返回结构化错误，不泄露密钥或内部连接字符串。
