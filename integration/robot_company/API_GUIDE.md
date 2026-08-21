# 机器人公司 API 接入使用手册

API：Companion Profile Engine v0.2.0  
接入方式：机器人公司服务端调用我方画像服务  
禁止方式：网页、机器人客户端或移动 App 直接携带 `API_KEY` 调用

## 1. 接入前取得三个参数

```text
BASE_URL=https://<我方提供的测试或生产域名>
TENANT_ID=<我方分配的租户标识>
API_KEY=<通过安全渠道单独提供>
```

测试和生产使用不同的地址、租户和 Key。机器人公司不得把 Key 写入前端代码、日志或普通聊天消息。

## 2. 鉴权规则

所有 `/v1` 请求都带：

```http
X-API-Key: <API_KEY>
X-Tenant-ID: <TENANT_ID>
```

所有 `POST` 写请求还必须带：

```http
Idempotency-Key: <租户内唯一且可稳定重试的业务操作 ID>
Content-Type: application/json
```

同一个业务操作重试时，幂等键和请求体必须保持不变；不同接口、不同用户或不同消息不要复用同一个幂等键。

## 3. 连通性检查

`GET /health` 不需要鉴权：

```bash
curl "$BASE_URL/health"
```

可用响应：

```json
{
  "status": "ok",
  "service": "companion-profile-engine",
  "version": "0.2.0",
  "services": {"application": "ok", "database": "ok"}
}
```

该接口在数据库不可用时仍可能返回 HTTP 200，但 `status` 会变成 `degraded`。健康判断必须检查 JSON 内容，不能只检查 HTTP 状态码。

## 4. 推荐调用流程

### 第一步：读取画像

```bash
curl "$BASE_URL/v1/profiles/$USER_ID" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Tenant-ID: $TENANT_ID"
```

- 返回 200：保存 `profile_version`。
- 返回 404：该用户还没有画像，执行初始化。

`USER_ID` 必须是机器人公司内部稳定、租户内唯一且不可复用的用户标识。不要直接使用手机号、昵称或会话 ID。

### 第二步：首次初始化

```bash
curl -X POST "$BASE_URL/v1/profiles:init" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Idempotency-Key: init:$USER_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_user_id": "robot-user-001",
    "display_name": "用户昵称",
    "consent": {
      "profile": true,
      "sensitive_inference": false
    }
  }'
```

只有取得用户画像授权后才能把 `profile` 设为 `true`。生日推断和九型数据需要 `sensitive_inference=true`；没有该授权时不要提交九型数据。

成功响应的关键字段：

```json
{
  "request_id": "req_xxx",
  "profile_version": 1,
  "profile": {},
  "rule_pack": {"version": "...", "sha256": "...", "status": "published"},
  "warnings": []
}
```

### 第三步：摄取本轮用户消息

```bash
curl -X POST "$BASE_URL/v1/profiles/$USER_ID/messages:ingest" \
  -H "X-API-Key: $API_KEY" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Idempotency-Key: $TURN_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "session-001",
    "message_id": "turn-001",
    "expected_profile_version": 1,
    "occurred_at": "2026-08-07T08:00:00Z",
    "text": "以后回答短一点，先听我把话说完。",
    "context": {
      "topic": "communication",
      "previous_turn_count": 0,
      "recent_turns": []
    }
  }'
```

字段要求：

- `conversation_id`：本次会话的稳定 ID。
- `message_id`：本轮用户消息的唯一 ID，推荐同时作为 `Idempotency-Key`。
- `expected_profile_version`：最近一次读取或写入成功后得到的版本。
- `occurred_at`：带时区的 ISO 8601 时间，推荐 UTC。
- `text`：当前用户原话，最多 10000 字符。
- `recent_turns`：可选的最近 user/assistant 历史，最多 12 条，每条最多 4000 字符。

成功响应中机器人侧主要消费：

```json
{
  "profile_version": 2,
  "reply_hints": {
    "max_sentences": 3,
    "answer_first": false,
    "empathy_first": true,
    "question_count": 1,
    "structure_level": "simple",
    "focus": "直接回应用户当前表达",
    "avoid": []
  },
  "profile_patch": [],
  "runtime_operations": [],
  "no_profile_change": false,
  "request_id": "req_xxx"
}
```

- `reply_hints`：生成本轮机器人回答时使用的语气、长度和结构建议。
- `profile_patch`：本轮稳定画像变化。
- `runtime_operations`：偏好、状态、事实或记忆变化。
- `no_profile_change=true`：本轮没有可靠变化，不是错误。
- `profile_version`：下一次写请求必须使用的新版本。

如果希望策略影响当前回答，应在调用聊天大模型前同步执行摄取并使用 `reply_hints`。如果优先降低聊天延迟，也可以先使用已有画像回答，再异步摄取；此时新策略从下一轮生效。

## 5. 用户更正和授权管理

| 用途 | 接口 |
| --- | --- |
| 查看画像依据 | `GET /v1/profiles/{user_id}/explain?field=<字段路径>` |
| 用户/专家明确更正 | `POST /v1/profiles/{user_id}:correct` |
| 写入已授权的九型测评 | `POST /v1/profiles/{user_id}:set-enneagram` |
| 失效记忆、证据、生日推断或九型 | `POST /v1/profiles/{user_id}:forget` |
| 撤回全部画像授权 | `POST /v1/profiles/{user_id}:forget`，`scope=all_profile` |
| 查询当前规则版本 | `GET /v1/rule-packs/current` |

`scope=all_profile` 会停止后续画像推断，清空运行时偏好、状态、记忆、数字密码和九型数据，并使证据/记忆失效；它不会物理删除核心画像快照、历史版本和审计记录。若需要物理删除，必须走双方另行约定的数据删除流程。

不要调用 `/v1/profiles/{user_id}:reset`。该接口只用于测试数据重置，不属于生产接入范围。

## 6. 版本冲突与重试

写请求使用乐观并发控制。收到 HTTP 409 时：

1. 重新 `GET /v1/profiles/{user_id}` 获取最新 `profile_version`。
2. 用新版本重试同一个业务操作，最多一次。
3. 保持原业务操作的幂等键；如果仍冲突，记录并进入人工/队列重试，不要无限循环。

网络超时且不知道服务是否已经处理时，使用完全相同的幂等键和请求体重试。

## 7. 错误处理

| HTTP | 含义 | 处理方式 |
| ---: | --- | --- |
| 401 | Key、租户错误或生产租户未配置 | 停止重试并检查配置 |
| 403 | 未取得画像/敏感推断授权，或画像已关闭 | 不自动重试 |
| 404 | 用户画像不存在 | 读取场景可转初始化 |
| 409 | `profile_version` 过期 | 重新读取后最多重试一次 |
| 422 | Header/字段错误，或幂等键复用到不同请求体 | 修正请求，不盲目重试 |
| 503 | 外部语义模型暂不可用 | 有限次数指数退避 |

每个响应都会带 `X-Request-ID` 响应头。报障时提供请求时间、路径、HTTP 状态和该 ID；不要提供 API Key、完整用户消息或完整画像。

## 8. 联调完成标准

1. `/health` 返回 `status=ok` 且 `database=ok`。
2. 错误 Key 返回 401。
3. 新用户能够初始化并读取到 `profile_version=1`。
4. 一轮消息能够返回 `reply_hints` 和新版本。
5. 相同幂等键、相同请求体重复发送得到相同结果。
6. 使用旧版本写入时返回 409。
7. 不同租户无法读取彼此用户。

完整机器可读契约见同包 `openapi.json`，可执行请求见 `postman_collection.json`，Node.js 18+ 示例见 `examples/quickstart.mjs`。
