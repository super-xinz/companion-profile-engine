# Profile Engine Zeabur 部署

完整双服务步骤在 HIWM 仓库 `docs/DEPLOYMENT_ZEABUR.md`。本文件确保画像仓库也可独立交付。

## Service

- Project：`companion-profile-engine`
- Service：`profile-api`
- Git branch：`main`
- Root Directory：`.`
- Dockerfile：`Dockerfile`（`zbpack.json` 已声明）
- Build：由 Zeabur 构建 Dockerfile，无控制台覆盖命令
- Effective Start：`sh -c "alembic upgrade head && profile-engine"`
- Health：`GET /health`

服务入口 `profile_engine.main:run` 监听 `0.0.0.0` 并优先读取 Zeabur 注入的 `PORT`。

## PostgreSQL

在同一 Project 创建 `profile-db` PostgreSQL。将平台提供的真实 connection string 映射为 `PROFILE_DATABASE_URL`。不要使用容器内 SQLite 保存生产画像；数据库不配置公共 Web 域名。

## 环境变量

| 变量 | 必需 | 敏感 | 说明 |
| --- | ---: | ---: | --- |
| `PROFILE_ENVIRONMENT=production` | 是 | 否 | 禁止开发 Key 回退 |
| `PROFILE_DATABASE_URL` | 是 | 是 | Zeabur PostgreSQL URL |
| `PROFILE_TENANT_API_KEYS` | 是 | 是 | `{"demo-tenant":"<随机 Key>"}` |
| `PROFILE_RULE_SOURCE_DIR=/app/rules` | 是 | 否 | 规则路径 |
| `PROFILE_DEMO_TENANT_ID=demo-tenant` | 是 | 否 | 与 HIWM 一致 |
| `PROFILE_DEMO_ACCESS_CODE` | 公开工作台时 | 是 | `/demo` 与 `/rules` 口令 |
| `PROFILE_DEMO_FEATURES_ENABLED` | 是 | 否 | 团队演示为 `true`；客户生产为 `false` |
| `PROFILE_API_DOCS_ENABLED` | 是 | 否 | 团队验收可开启；客户生产默认关闭 |
| `PROFILE_ALLOW_PROFILE_RESET` | 是 | 否 | 仅测试环境允许 |
| `PROFILE_RATE_LIMIT_PER_MINUTE` | 是 | 否 | 单副本租户级基础限流 |
| `PROFILE_SEMANTIC_EXTRACTOR=model` | 是 | 否 | 外部模型语义抽取 |
| `PROFILE_DEFAULT_MODEL_PROVIDER=deepseek` | 是 | 否 | 默认模型，可选 `deepseek` / `claude` / `gpt` / `glm` / `gemini` / `kimi` |
| `PROFILE_OPENROUTER_API_KEY` | 是 | 是 | 所有模型共用的 OpenRouter 密钥 |
| `PROFILE_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1` | 是 | 否 | OpenRouter 兼容接口 |
| `PROFILE_DEEPSEEK_MODEL=deepseek/deepseek-v3.2` | 是 | 否 | 固定 DeepSeek V3.2 |
| `PROFILE_CLAUDE_MODEL=~anthropic/claude-sonnet-latest` | 是 | 否 | Claude Sonnet 路由，可固定具体版本 |
| `PROFILE_GPT_MODEL=~openai/gpt-latest` | 是 | 否 | OpenRouter GPT 最新路由 |
| `PROFILE_GLM_MODEL=z-ai/glm-5.2` | 是 | 否 | OpenRouter GLM 5.2 |
| `PROFILE_GEMINI_MODEL=~google/gemini-pro-latest` | 是 | 否 | OpenRouter Gemini Pro 最新路由 |
| `PROFILE_KIMI_MODEL=~moonshotai/kimi-latest` | 是 | 否 | OpenRouter Kimi 最新路由 |
| `PROFILE_ALLOW_EXTERNAL_SEMANTIC_PROCESSING=true` | 是 | 否 | 取得同意后启用 |

## 域名与服务访问

优先只让 HIWM Service 通过同 Project 内部地址访问。本地区域若没有可用内部 HTTP 地址，可给 profile-engine 生成不公开宣传的 HTTPS 域名；核心读写仍强制 `X-API-Key`、`X-Tenant-ID`，写操作再强制 `Idempotency-Key`。无需为浏览器开放 CORS。

## 验收

1. Build Logs 无失败，Runtime Logs 显示 Alembic 完成和 Uvicorn 启动。
2. `/livez`、`/readyz` 返回 200，`/health` 返回 `status=ok`、`services.database=ok`。
3. 用无效 Key 请求 `/v1/profiles/demo-xu` 返回 401。
4. 用有效 Key 初始化、读取、摄取、读取新版本、重置均成功。
5. PostgreSQL/Service 重启后画像仍存在。
6. 日志只包含 request_id、path、状态和耗时，不含 Key/Header/完整画像。
7. `GET /v1/capabilities` 返回服务 0.5.0、API v1、Schema、规则包与可选模型配置。
8. 客户生产环境的 `/demo`、`/rules`、`/docs` 与 `:reset` 均不可访问。

## 更新与回滚

GitHub `main` push 自动部署，或在 Dashboard 手动 Redeploy。回滚到上一个稳定 commit 前，先核对该版本与现有 Alembic schema 的兼容性；数据库使用平台备份恢复，不要通过删除迁移文件回滚。环境变量修改后重新部署并再次跑冒烟脚本。

当前 Zeabur `profile-api` 实例是手工上传来源，控制台显示 `Reupload`，不会自动跟随 GitHub 更新。团队短期可继续上传不含 `.env`、`.git`、虚拟环境和本地数据库的源码包；长期应改为连接私有 GitHub 仓库并启用受控的 main/tag 部署。

官方参考：[Dockerfile 部署](https://zeabur.com/docs/en-US/deploy/methods/dockerfile)、[环境变量](https://zeabur.com/docs/en-US/deploy/config/environment-variables)、[公网域名](https://zeabur.com/docs/en-US/deploy/networking/public-networking)。
