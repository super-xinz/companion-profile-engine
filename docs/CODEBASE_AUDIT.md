# Companion Profile Engine 代码库审计

审计日期：2026-08-01。审计对象仅为 `companion-profile-engine` 仓库；以下结论以仓库中的真实代码、配置和测试为准。

## 结论

本仓库是可独立构建和部署的 FastAPI 画像服务，同时包含受口令保护的演示与专家工作台。生产画像使用 PostgreSQL 持久化；浏览器工作台与 B 端核心 API 使用不同的鉴权边界。核心算法、规则、证据、审计和数据模型均由本服务维护，不需要复制到调用方系统。

建议生产拓扑：

| 组件 | 形态 | 职责 |
| --- | --- | --- |
| `profile-api` | `companion-profile-engine` 容器 | 画像状态机、规则、证据、审计及可选工作台 |
| `profile-db` | PostgreSQL | 生产画像、版本、证据与审计持久化 |
| B 端调用方 | 客户自己的服务端 | 保管租户密钥、编排对话模型与画像 API、生成最终回复 |

## 技术与入口

- Python、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic。
- 命令入口：`profile-engine` → `profile_engine.main:run`；监听 `0.0.0.0`，优先读取平台注入的 `PORT`。
- HTTP 路由：`src/profile_engine/api.py`、`demo.py`、`workspace.py`。
- 请求与响应结构：`src/profile_engine/schemas.py`。
- 开发环境可使用 SQLite；生产通过 `PROFILE_DATABASE_URL` 连接 PostgreSQL。
- Docker 启动先执行 `alembic upgrade head`，随后运行 `profile-engine`。

## API 与鉴权边界

- 核心 `/v1` API 使用 `X-API-Key` 与 `X-Tenant-ID`；写操作还要求唯一的 `Idempotency-Key`。
- Demo/Workspace 使用 `X-Demo-Code`，不复用也不暴露 B 端租户密钥。
- 画像初始化、读取、消息摄取、证据解释、人工更正、确认信息设置、按范围遗忘、重置、删除及规则包接口均在本仓库实现。
- 写入采用画像版本校验；并发版本不一致返回 HTTP 409，调用方应先重新读取再决定是否重试。
- 详细请求、响应、错误语义与 curl 示例见 `docs/API_USAGE.md`。

## 数据与模型边界

- 规则引擎负责证据校验、冲突处理、置信度、单轮限幅、版本与审计。
- 外部语言模型只能返回结构化候选和回复策略，不能绕过规则直接写数据库。
- 调用方服务端应从画像响应中选择对话所需字段，并设置字段白名单和长度上限；不要把完整审计记录交给终端或直接注入模型。
- 日志只应记录 request ID、方法、路径、状态码和耗时，不应记录 API Key、消息正文或完整画像。

## 部署与运行

- Zeabur 使用仓库根目录的 `Dockerfile` 和 `zbpack.json`。
- 生产必须注入 PostgreSQL 连接、租户密钥映射、规则路径及所需模型配置；真实密钥不得进入 Git、镜像或公开文档。
- `/livez` 用于存活检查，`/readyz` 用于就绪检查，`/health` 同时验证数据库连接。
- 客户生产默认关闭 Demo、规则工作台、API 文档和画像重置；需要演示时使用隔离租户、独立口令和明确的访问范围。
- 完整环境变量与验收步骤见 `docs/DEPLOYMENT_ZEABUR.md`。

## 风险与交付边界

- `source_profiles/` 含资料文件，对外分发或公网展示前必须确认授权并按需要匿名化。
- 人物画像、消息、证据和审计均属于敏感数据，生产数据库应启用最小权限、备份、加密与保留期限策略。
- 如果启用外部语义模型，原始消息可能发送给模型供应商；产品侧必须完成告知、授权与删除流程。
- 访问口令和 API Key 只通过环境变量或受控密钥管理器配置；Postman Collection 只保留占位值。
