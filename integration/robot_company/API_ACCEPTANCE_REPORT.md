# 机器人公司 API 交付验收报告

项目：Companion Profile Engine  
API 版本：v0.2.0  
验收日期：2026-08-07  
验收范围：对外核心 `/v1` API、安装、数据库迁移、鉴权、租户隔离、幂等与画像主流程

## 验收结论

核心 API 功能正常，可以交付机器人公司进入服务端联调。

它不是一个交给对方即可无配置上线的公共 API。正式联调前，我方必须先部署服务，并向对方提供 `BASE_URL`、`TENANT_ID` 和对应 `API_KEY`；正式生产前还必须完成 PostgreSQL、HTTPS、访问控制、备份与监控配置。

## 已完成验证

| 验证项 | 结果 |
| --- | --- |
| 从空白 Python 虚拟环境安装全部项目/测试依赖 | 通过 |
| 按 Dockerfile 方式构建 wheel 并执行非 editable `pip install .` | 通过 |
| 安装后从 `site-packages` 导入 API 和静态资源 | 通过 |
| 隔离环境依赖完整性 `pip check` | 通过，无破损依赖 |
| SQLite 执行全部 Alembic 迁移 | 通过，到 `20260723_002 (head)` |
| 全量自动化测试 | 20/20 通过 |
| 真实 Uvicorn HTTP 服务启动 | 通过 |
| `/health` 应用与数据库检查 | 通过 |
| 生产模式租户 API Key 鉴权 | 通过 |
| 错误 Key 返回 401 | 通过 |
| 用户不存在返回 404 | 通过 |
| 未授权画像返回 403 | 通过 |
| 初始化、读取、消息摄取主链路 | 通过 |
| `reply_hints` 返回当前回复策略 | 通过 |
| 初始化和消息摄取幂等重试 | 通过 |
| 同一幂等键复用到不同请求体时拒绝 | 通过，返回 422 |
| 画像版本冲突 | 通过，返回 409 |
| 两个租户的数据隔离 | 通过 |
| 人工更正与画像解释 | 通过 |
| 九型设置和撤回 | 通过 |
| 全部画像授权撤回后禁止继续推断 | 通过 |
| 当前规则包查询 | 通过 |

真实 HTTP 验收共执行 23 个断言，全部通过。

## 已验证的源码启动方式

对方如需自行部署完整项目，应取得完整私有源码和环境变量，而不只是本 API 接入包：

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install .
cp .env.example .env
alembic upgrade head
profile-engine
```

Python 依赖安装、wheel 构建、迁移和服务入口均已验证。生产环境不能直接使用 `.env.example` 中的开发 Key 和 SQLite 配置。

## 当前可交付接口

- `POST /v1/profiles:init`
- `GET /v1/profiles/{user_id}`
- `POST /v1/profiles/{user_id}/messages:ingest`
- `GET /v1/profiles/{user_id}/explain`
- `POST /v1/profiles/{user_id}:correct`
- `POST /v1/profiles/{user_id}:set-enneagram`
- `POST /v1/profiles/{user_id}:forget`
- `GET /v1/rule-packs/current`
- `GET /health`

`POST /v1/profiles/{user_id}:reset` 已验证可用，但它是测试数据重置接口，不属于机器人公司的生产接入范围，也未放入交付版 OpenAPI。建议生产网关直接禁止该路径。

## 上线前必须完成

1. 使用 `PROFILE_ENVIRONMENT=production`，为每个合作方配置独立 `PROFILE_TENANT_API_KEYS`。
2. 使用 PostgreSQL 生产数据库；SQLite 只用于本地开发和联调验证。
3. 在 API 网关或反向代理启用 HTTPS、来源限制、请求体大小限制和访问日志脱敏。
4. 明确使用 `deterministic` 还是 `qwen` 语义抽取器。使用 Qwen 时必须配置模型 Key，并取得用户对原始对话外发处理的授权。
5. 确认超时、QPS、可用性、数据保留期、备份恢复和故障联系人；当前代码没有对外承诺这些 SLA 数值。
6. 确认用户删除口径：`scope=all_profile` 会关闭推断，清空运行时偏好、状态、记忆、数字密码和九型数据，并使证据/记忆失效；它不会清除核心画像快照、历史版本和审计记录。如果合同要求物理删除全部个人数据，需要在上线前另行实现和验收硬删除流程。

## 本次未完成的验证

- 当前机器未安装 Docker，因此没有执行 Docker 镜像构建和 Docker Compose 实机启动。
- 本次没有连接真实 PostgreSQL 实例。
- 本次没有调用真实 Qwen 外部模型服务。
- 未进行压力测试、渗透测试、长时间稳定性测试或灾备恢复演练。

以上项目不影响当前接口联调，但不能据此宣称已经完成生产级容量、安全和 SLA 验收。
