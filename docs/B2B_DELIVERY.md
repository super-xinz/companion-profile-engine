# Companion Profile Engine B 端交付方案

## 1. 推荐环境边界

同一套代码部署为两个逻辑环境，不要把团队演示后台直接当成客户生产 API：

| 环境 | 访问者 | Demo/规则工作台 | OpenAPI | 整用户重置 | 数据库 |
| --- | --- | --- | --- | --- | --- |
| 团队验收/演示 | 产品、研发、Leader | 开启并使用强访问码 | 开启 | 按需开启 | 独立 PostgreSQL |
| 客户生产 API | 客户服务端 BFF | 关闭 | 默认关闭或仅内网开放 | 关闭 | 独立 PostgreSQL |

当前 Zeabur `companion-profile-engine` 项目适合作为团队验收环境。正式客户建议新建 Project 或至少新建独立 Service + PostgreSQL，避免演示人物、规则操作和客户真实数据混在一起。

## 2. 客户获得什么

客户接入包应包含：

- HTTPS Base URL，例如 `https://profile-api.example.com`；
- 独立的 `tenant_id` 与至少 32 字符随机 API Key；
- OpenAPI JSON、Postman Collection 和 API 使用文档；
- 服务版本、画像 Schema 版本、规则包版本及变更记录；
- 测试租户、测试用户和验收脚本；
- 数据处理说明、删除/遗忘流程、故障与支持联系人；
- 约定的限流、超时、重试、SLA 和版本弃用周期。

API Key 只能保存在客户服务端，不能进入网页、App 包、日志、工单或埋点。浏览器只调用客户自己的 Chat BFF。

## 3. 标准接入流程

1. 客户后端启动时调用 `GET /v1/capabilities`，记录服务、Schema 与规则版本。
2. 用户首次授权画像时调用 `POST /v1/profiles:init`。
3. 每轮对话先取得或缓存当前 `profile_version`。
4. 调用 `POST /v1/profiles/{user_id}/messages:ingest`，传用户消息、当前版本和必要的最近轮次。
5. 使用返回的 `reply_hints`、新版画像必要字段和用户消息调用客户自己的语言模型 API。
6. `409` 时重新读取画像版本并最多重试一次；`429` 遵循 `Retry-After`；`503` 使用相同幂等键有限退避重试。
7. 用户更正或删除数据时使用 `:correct`、`:forget`，不要直接操作画像数据库。

所有写请求必须提供唯一 `Idempotency-Key`。同一逻辑请求重试时保持不变，不同请求绝不能复用。

## 4. 生产环境变量基线

```text
PROFILE_ENVIRONMENT=production
PROFILE_DATABASE_URL=postgresql://...
PROFILE_TENANT_API_KEYS={"customer-a":"至少32字符随机密钥"}
PROFILE_RULE_SOURCE_DIR=/app/rules
PROFILE_SEMANTIC_EXTRACTOR=model
PROFILE_DEFAULT_MODEL_PROVIDER=deepseek
PROFILE_OPENROUTER_API_KEY=...
PROFILE_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
PROFILE_DEEPSEEK_MODEL=deepseek/deepseek-v3.2
PROFILE_CLAUDE_MODEL=~anthropic/claude-sonnet-latest
PROFILE_ALLOW_EXTERNAL_SEMANTIC_PROCESSING=true
PROFILE_DEMO_FEATURES_ENABLED=false
PROFILE_API_DOCS_ENABLED=false
PROFILE_ALLOW_PROFILE_RESET=false
PROFILE_RATE_LIMIT_PER_MINUTE=120
```

团队验收环境可以把三个功能开关设置为 `true`，但必须配置强 `PROFILE_DEMO_ACCESS_CODE`，并且不得使用客户生产数据库。

## 5. 当前安全与可靠性保证

- 租户 ID 与租户 Key 联合鉴权，所有人物查询带租户边界；
- 生产配置启动前 fail-closed 校验；
- 乐观版本控制避免并发覆盖；
- 幂等记录避免网络重试重复更新；
- 模型只能给候选，硬规则决定是否写入画像；
- 人工更正、遗忘、证据、规则版本和前后快照进入审计；
- 每租户基础限流，返回剩余额度与重试时间；
- API 响应禁止缓存，并设置 HSTS、反嵌入和内容类型保护；
- `/livez` 检查进程，`/readyz` 检查数据库可用性；
- Demo、规则管理、OpenAPI 和测试重置可在生产完全关闭。

当前进程内限流适合 Zeabur 单副本。扩展到多副本或需要商业计量时，应由 API Gateway/Redis 实现全局配额与用量账单。

## 6. 数据与合规决策

上线前必须由业务方明确：

- 原始对话是否允许发送到百练、数据所在区域和保留期限；
- 哪些用户可以启用画像和敏感推断，如何撤回授权；
- 客户数据保留、备份、导出和彻底删除周期；
- source_profiles 中五份资料是否获准进入该部署；
- 日志、数据库备份及运维人员的最小权限；
- 数据泄露、模型供应商故障和错误画像申诉流程。

建议客户生产部署移除五份真实模板资料，替换成匿名合成案例；模板资料不应与客户真实用户共库。

## 7. 上线验收

- `/livez`、`/readyz` 均为 200，数据库重启后画像仍存在；
- 缺 Key、错误 Key、错租户均为 401，租户间无法读取同名 user_id；
- 同一幂等键重复请求结果一致，不同请求体复用该键为 422；
- 并发旧版本更新为 409；超限为 429 且带 `Retry-After`；
- 回复偏好、当前状态和长期特质不会越层写入；
- `:correct`、`:forget`、规则回滚和审计查询可用；
- Demo、规则页面、OpenAPI、`:reset` 在客户生产环境不可访问；
- 日志不含 API Key、完整画像或原始消息；
- 版本升级前完成数据库备份和向后兼容测试。

## 8. 商务化仍需补充

代码已具备 B 端技术接入基础，但正式商业交付还需要产品/合同层确定 SLA、计费单位、租户配额、技术支持时间、版本弃用周期、DPA/隐私条款，以及多副本情况下的集中式限流与监控告警。
