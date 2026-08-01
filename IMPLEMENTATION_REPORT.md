# Companion Profile Engine 实施报告

日期：2026-08-01

## 交付结论

画像引擎已从“可独立演示的画像 API”完善为可供 HIWM 陪伴聊天稳定调用的真实后端：保留原有鉴权、租户隔离、幂等与乐观锁语义，新增可确认、可幂等的画像重置能力，补充数据库级健康检查、结构化请求日志、完整 API 文档、Postman Collection、Zeabur 配置和跨平台冒烟脚本。

## 关键实现

- `POST /v1/profiles/{user_id}:reset`：必须提交 `{"confirm": true}`，删除该租户用户的画像关联数据后以同一业务 ID 重建空白画像；写操作继续要求 `Idempotency-Key`。
- `/health`：执行 `SELECT 1` 验证数据库可用性，区分应用与数据库状态。
- 请求日志：只记录 request ID、方法、路径、状态码与耗时，不记录 API Key、Header、消息正文或完整画像。
- 使用文档：覆盖初始化、读取、消息摄取、解释、更正、九型、遗忘、重置、规则包、Demo 与工作台接口，并提供错误语义和 curl 示例。
- 部署：`zbpack.json` 固定使用现有生产 `Dockerfile`；启动时先执行 Alembic，再监听平台注入的 `PORT`。

## 与 HIWM 的协同边界

浏览器不直接访问画像引擎。HIWM BFF 使用 `X-API-Key`、`X-Tenant-ID` 和每轮唯一幂等键调用画像 API；读取失败时允许聊天降级，画像写回失败时保留模型回复并允许单独重试。画像引擎不生成聊天回复，也不接收浏览器凭证。

## 验证结果

- 全量后端测试：`15 passed`（`PYTHONPATH=src python -m pytest -q`）。
- 独立 API 冒烟：健康检查、初始化、读取、中文偏好摄取、再次读取均通过；画像版本从 v1 前进到 v2。
- 双服务联调：HIWM 经 mock OpenAI-compatible 服务完成真实 BFF 编排，画像版本从 v1 前进到 v2。
- JSON 资产、Python 编译和前端联调结果记录在 HIWM 的实施报告中。

## 尚需部署人员完成

当前工作区没有目标 Zeabur/GitHub 的发布权限，因此没有虚构线上 URL 或部署状态。部署人员需按 `docs/DEPLOYMENT_ZEABUR.md` 创建 PostgreSQL、注入密钥、发布服务并执行线上冒烟；任何真实密钥都不得提交到 Git。
