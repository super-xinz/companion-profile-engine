# B 端接入包

适用范围：B 端从自己的服务端接入画像引擎。不要从浏览器或 App 直连，否则 `X-API-Key` 会泄露。

## 我方必须交付的 3 个值

| 配置 | 测试环境 | 生产环境 |
| --- | --- | --- |
| `PROFILE_ENGINE_BASE_URL` | 由部署负责人填写 | 由部署负责人填写 |
| `PROFILE_ENGINE_TENANT_ID` | 由画像引擎负责人分配 | 由画像引擎负责人分配 |
| `PROFILE_ENGINE_API_KEY` | 通过密码管理器等安全渠道发送 | 通过密码管理器等安全渠道发送 |

API Key 不得发在群聊、工单正文或代码仓库中。测试和生产必须使用不同 Key。

## B 端真正要接的接口

正常聊天链路只需要前三个接口；后四个用于用户更正、授权与删除能力。

| 用途 | 方法与路径 | 何时调用 |
| --- | --- | --- |
| 读取画像 | `GET /v1/profiles/{user_id}` | 每轮组织模型上下文前；404 时初始化 |
| 初始化 | `POST /v1/profiles:init` | 用户首次开启画像功能 |
| 摄取消息 | `POST /v1/profiles/{user_id}/messages:ingest` | 一轮用户消息处理完成后 |
| 解释依据 | `GET /v1/profiles/{user_id}/explain` | 用户查看画像依据时 |
| 人工更正 | `POST /v1/profiles/{user_id}:correct` | 用户或专家明确更正时 |
| 设置九型 | `POST /v1/profiles/{user_id}:set-enneagram` | 已有明确测评/专家确认且获得敏感推断授权时 |
| 遗忘/关闭 | `POST /v1/profiles/{user_id}:forget` | 用户撤回授权或要求删除时 |

`POST /v1/profiles/{user_id}:reset` 是测试数据重置接口，不要接入生产业务流程。

## 固定请求规则

所有 `/v1` 请求都带：

```http
X-API-Key: <API_KEY>
X-Tenant-ID: <TENANT_ID>
```

所有 `POST` 写操作还要带：

```http
Idempotency-Key: <同一业务操作保持不变、不同操作必须唯一>
Content-Type: application/json
```

推荐直接复用 B 端的稳定 ID：初始化使用 `init:{user_id}`；消息摄取使用 `turn_id`；更正和删除使用对应操作 ID。`user_id` 必须是租户内稳定且不可复用的用户标识，不能使用昵称、手机号明文或会话 ID。

## 标准聊天时序

1. `GET profile`；如果是 404，则在取得 `consent.profile=true` 后执行 `profiles:init`。
2. 从画像中只选取本轮需要的字段放入大模型上下文；不要把整份画像原样注入。
3. 正常调用聊天模型并把回复返回用户。
4. 回复完成后摄取本轮用户消息；`conversation_id=session_id`，`message_id=turn_id`，`Idempotency-Key=turn_id`。
5. 保存响应中的 `profile_version`。下一次写入必须传当前版本。

消息摄取对象：

```json
{
  "conversation_id": "session-001",
  "message_id": "turn-001",
  "expected_profile_version": 1,
  "occurred_at": "2026-08-02T12:00:00Z",
  "text": "以后回答短一点，先听我把话说完。",
  "context": {
    "topic": "communication",
    "previous_turn_count": 0,
    "recent_turns": []
  }
}
```

聊天侧优先消费摄取响应中的 `reply_hints`；`profile_patch` 是稳定画像变化，`runtime_operations` 是偏好、状态或记忆变化。不要根据 `semantic_frames` 自行改画像。

## 并发、重试与错误

| HTTP | B 端处理 |
| ---: | --- |
| `401` | 凭据或租户配置错误，报警，不自动重试 |
| `403` | 缺少用户授权，停止画像处理 |
| `404` | 读取画像时可转初始化；其他接口不重试 |
| `409` | 重新读取最新画像版本，使用同一业务幂等键最多重试 1 次 |
| `422` | 请求字段错误，记录 `X-Request-ID` 并修正代码，不重试 |
| `503` | 外部语义服务暂不可用，指数退避有限重试 |

连接超时、总超时、QPS 和 SLA 目前代码中没有正式承诺，不能由接入方猜测。上线前双方必须书面确认这些值。排障时同时提供响应头 `X-Request-ID`、时间、路径和 HTTP 状态，禁止附带 API Key 或完整用户消息。

## 本目录文件

- `openapi.json`：从当前 FastAPI 代码导出的核心接口契约，可导入代码生成器或 API 平台。
- `companion-profile-engine.postman_collection.json`：不含真实密钥的联调集合，导入后只需填写 3 个交付值。
- `client.env.example`：B 端服务配置模板。
- `examples/quickstart.mjs`：Node.js 18+ 可运行示例，演示读取/初始化、摄取和 409 重试。

联调通过标准：`GET /health` 的 `status=ok`；能完成一个新用户的初始化、读取和一轮消息摄取；重复发送相同 `Idempotency-Key` 与相同 body 得到相同响应；过期版本得到 409；错误租户/Key 得到 401。
