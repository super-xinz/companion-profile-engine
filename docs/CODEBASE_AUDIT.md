# 两项目代码库审计

审计日期：2026-08-01。审计对象是两个彼此独立、工作树均干净的 Git 仓库：`companion-profile-engine` 与相邻的 `HIWM`。提示词中预计的 React/Next.js/Tailwind 并非真实技术栈，实施以代码为准。

## 结论与部署模式

采用两个 Zeabur Service，而不是三服务或强制 Monorepo：

| Service | 仓库 | 真实职责 |
| --- | --- | --- |
| `web-chat-api` | `HIWM` | Vue 3/Vite Web UI、FastAPI Chat BFF、原 OpenAI-compatible LLM、会话 SQLite |
| `profile-engine` | `companion-profile-engine` | FastAPI 画像状态机、规则、证据、审计、PostgreSQL/SQLite |
| `profile-db` | Zeabur PostgreSQL | 生产画像持久化；不是 Web Service |

`HIWM` 本来就是由 Python FastAPI 承载已构建 Vite 静态资源的同源应用，因此合并 web 与 Chat API 可以避免 CORS 和浏览器密钥泄漏。画像引擎继续独立，核心算法不被搬入 HIWM。

## companion-profile-engine

- Python 3.9+，FastAPI、Pydantic v2、SQLAlchemy 2、Alembic。
- 入口：`profile-engine` → `profile_engine.main:run`；监听 `0.0.0.0`，优先读取 `PORT`。
- 路由：`src/profile_engine/api.py`、`demo.py`、`workspace.py`。
- Schema：`src/profile_engine/schemas.py`。
- 存储：开发默认 SQLite；生产支持 `PROFILE_DATABASE_URL` 指向 PostgreSQL。
- 鉴权：核心 `/v1` 使用 `X-API-Key` + `X-Tenant-ID`；写操作再要求 `Idempotency-Key`。Demo/Workspace 使用 `X-Demo-Code`。
- 画像已有能力：初始化、读取、对话摄取、证据解释、人工更正、九型设置、按范围遗忘、规则包和专家工作台。
- 测试：pytest/FastAPI TestClient。
- Docker：容器启动先执行 `alembic upgrade head`，随后运行 `profile-engine`。

本次仅补充 Chatbot 适配所需的安全用户重置、数据库健康状态、结构化请求日志、Zeabur 配置和文档；不修改规则编译、画像评分、语义提取、九型模型或专家审批流。

## HIWM

- Python 3.11 FastAPI/Gradio/FastRTC 后端；Vue 3 + TypeScript + Pinia + Vite 前端；pnpm 10 锁文件。
- 入口：`python src/demo.py`；Zeabur 使用 `scripts/start_zeabur.sh`，监听 `0.0.0.0:$PORT`。
- 前端挂载：`src/service/frontend_service/frontend_service.py` 将 `frontend/dist` 挂到 `/ui`。
- 原模型接入：`src/handlers/llm/openai_compatible`、`src/handlers/hiwm/world_model.py`，均使用服务端 OpenAI-compatible API。
- 可复用界面：HIWM 顶部身份、实时互动页面、聊天记录、画像输入、Cognitive Stream、服务配置状态、响应方案三栏布局和响应式视觉语言。
- 既有持久化主要是浏览器状态与 HIWM ledger，不满足独立文字 Chatbot 会话恢复。
- 测试：pytest；前端使用 Node test runner，另有 vue-tsc/ESLint。

本次新增 `src/service/companion_service` 作为 BFF，复用现有 FastAPI 进程和 OpenAI-compatible 运行时，不让浏览器直接访问 LLM 或画像密钥。新增 Vue 画像聊天控制中心，同时保留原实时互动入口。

## 真实核心 API

画像核心 API 详见 `docs/API_USAGE.md`。新增 HIWM BFF 路由为：

- `POST /api/v1/access/login`、`GET /api/v1/access/status`、`POST /api/v1/access/logout`
- `GET /api/health`、`GET /api/v1/companion/health`
- `POST /api/v1/companion/chat`、`POST /api/v1/companion/chat/stream`
- `GET /api/v1/companion/profile/{user_id}`
- `POST /api/v1/companion/profile/{user_id}/reset`
- `GET|DELETE /api/v1/companion/sessions/{session_id}`（GET 使用 `/messages`）
- `POST /api/v1/companion/sessions/{session_id}/turns/{turn_id}/profile-update:retry`

## 原先缺失、现已补齐

- 服务端双 API 顺序编排和 Adapter 边界。
- 持久化文字会话与 `turn_id` 去重。
- 画像读取失败降级、写回失败保留回复和人工重试。
- 服务端 Demo 口令、HttpOnly Session、5 分钟失败次数限制。
- 同源 SSE 流式对话及停止生成。
- 画像概览、变化摘要、服务状态、耗时、开关、确认重置。
- Zeabur 两服务构建、环境变量和持久化说明。

## 风险与边界

- 当前没有 Zeabur 账号或目标 GitHub 仓库授权，不能声称线上部署完成。
- HIWM 实时音视频需要 TURN、浏览器媒体权限及供应商 ASR/TTS；文字画像聊天不依赖摄像头。
- HIWM 文字会话使用 SQLite，Zeabur 必须把 `/data` 绑定持久化卷；画像使用 PostgreSQL。
- `source_profiles/` 含已授权资料，公开仓库或公网展示前必须再次确认数据授权。
- 访问 Session 存于进程内存；重新部署后用户需重新输入口令，这是有意的安全行为。
