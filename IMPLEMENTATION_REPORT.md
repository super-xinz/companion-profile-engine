# Companion Profile Engine 实施报告

日期：2026-08-21

## 交付结论

画像引擎已完善为可供 B 端服务稳定调用的真实后端，并提供受口令保护的中性演示页。核心 `/v1` 鉴权、租户隔离、幂等与乐观锁语义保持不变；公开页面只获得白名单展示数据，不接收原始画像、来源资料、规则、模型配置或执行轨迹。

## 关键实现

- `POST /v1/profiles/{user_id}:reset`：必须提交 `{"confirm": true}`，删除该租户用户的画像关联数据后以同一业务 ID 重建空白画像；写操作继续要求 `Idempotency-Key`。
- `/health`：执行 `SELECT 1` 验证数据库可用性，区分应用与数据库状态。
- 请求日志：只记录 request ID、方法、路径、状态码与耗时，不记录 API Key、Header、消息正文或完整画像。
- 使用文档：覆盖初始化、读取、消息摄取、解释、更正、画像维护、遗忘、重置、规则包与 Demo 接口，并提供错误语义和 curl 示例。
- 部署：`zbpack.json` 固定使用现有生产 `Dockerfile`；启动时先执行 Alembic，再监听平台注入的 `PORT`。

## B 端接入边界

B 端应由自己的服务端使用 `X-API-Key`、`X-Tenant-ID` 和每轮唯一幂等键调用画像 API，不应把画像 API 密钥下发给浏览器或机器人终端。画像读取失败时，调用方可按自身业务决定是否降级；写回失败时应保留业务回复并允许单独重试。画像引擎维护画像状态并返回回复提示，不代替调用方生成最终自然语言回复。

## 验证结果

- 全量后端测试：`54 passed`（`PYTHONPATH=src python -m pytest`）。
- 独立 API 冒烟：健康检查、初始化、读取、中文偏好摄取、再次读取均通过；画像版本从 v1 前进到 v2。
- 接入链路验证：通过 mock OpenAI-compatible 服务完成服务端编排，画像版本从 v1 前进到 v2。
- Postman Collection 已通过 JSON 解析校验；核心验证命令与线上验收步骤分别记录在 API 和部署文档中，发布前应针对待部署 commit 重新执行。

## 部署状态

本次版本使用既有 Zeabur PostgreSQL 与 `profile-engine` Service 原地升级，没有重建或删除 B 端生产数据。

- 最终 Zeabur Deployment ID：`6a880c58a158dec405726b72`，状态 `RUNNING`。
- 主站：`https://companion-profile-engine.zeabur.app/demo`；根路径跳转到该受口令保护的演示页。
- 健康检查：`GET /health` 返回 200。
- B 端回归：生产租户 `GET /v1/capabilities` 返回 200，`X-API-Version: 1`，既有租户 ID、数据库与 API 契约保持不变。
- 五案例验收：固定返回 5 个中性别名；每例包含 17 项中性指标、独立摘要及 5 项沟通偏好；重复 Bootstrap 不新增空会话。
- 真实聊天验收：返回 200 且生成有效回复；公开响应仅含回复、画像版本与中性更新摘要。上游瞬时网络错误和 429/5xx 会做一次有界重试。
- 公开面验收：`/demo` 及可下载 JS/CSS 的敏感标识扫描为 0 命中；原始 HTML、规则资源、源码映射、规则台、API 文档、OpenAPI 与旧管理接口全部返回 404。
- 单项目收敛：旧额外网站域名已移除，旧服务已暂停（可恢复，未删除其服务或数据）。
- 凭据管理：Demo 口令、B 端 API Key 与模型 Key 只存在于 Zeabur 服务端变量，未写入 Git、网页或 API 响应。
