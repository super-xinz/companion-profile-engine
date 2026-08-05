# 共鸣画像引擎

本次双项目协同进化的交付入口：[系统使用与交付说明](docs/SYSTEM_USAGE_AND_DELIVERY.md)、[API 使用文档](docs/API_USAGE.md)、[B 端交付方案](docs/B2B_DELIVERY.md)、[Zeabur 部署](docs/DEPLOYMENT_ZEABUR.md)、[实施报告](IMPLEMENTATION_REPORT.md) 与 [Postman Collection](postman/companion-profile-engine.postman_collection.json)。

面向陪伴机器人与心理／学习专家协作的多人画像管理平台。系统将规则、人物、长期画像、证据、对话和人工修正统一管理；模型只能提出候选，只有通过规则校验的内容才会进入画像。

> 本仓库不包含访问密码、数据库凭据或模型 API Key。请仅通过环境变量注入这些配置。

## 产品能力

### 对话与画像工作台

- 多人物、多对话管理：每个人的画像、长期事实、偏好、短期状态、证据和聊天记录彼此隔离。
- 完整画像：展示 17 个核心维度、MBTI 连续维度、身份事实、记忆、状态、证据、置信度、更新时间与版本。
- 人工修正：支持更正事实、调整画像、遗忘记忆和标记错误推断；所有更正都进入审计记录，并优先于普通模型推断。
- 聊天过程可追溯：查看语义理解、命中规则、画像变化和回答策略。
- 单一访问密码：在同一浏览器标签页中输入一次即可在两个工作台间切换。

### 规则管理工作台

- 以完整文档方式维护四份规则资产，而不是把规则拆成难以理解的零散字段。
- 支持草稿、保存、提交审核、批准、正式发布、版本差异比较与回滚。
- 在发布前校验文档结构、字段引用、规则格式及冲突。
- 提供隔离测试：对模拟对话并排展示新旧规则的理解、规则命中、画像变化和回答策略，不改动真实画像。
- 支持团队成员和角色权限：管理员、审核人、画像专家、只读成员。

### 画像与模型边界

- 17 个核心画像维度、MBTI 连续维度、长期记忆、交互偏好与短期状态。
- 数字密码画像将生日归约为四位码，覆盖 1458 种组合，并按不同权重生成性格、行为、做事工作和关系情感四类低置信度先验。
- 九型互动画像支持 9 个主型、18 个侧翼、6 种本能叠层、54 种组合与 10 类场景适配。
- 九型身份只接受用户明确声明、已授权外部测评或专家确认；不会由 MBTI、生日、单一行为或普通对话自动分类。
- 数字密码画像保留 26 个展开后的加权成分及来源摘要；用户事实、人工修正和对话证据始终优先。
- 主型、侧翼、本能、当前状态和场景分别生成动机、注意力、表达、状态与互动策略；用户明确偏好和安全规则优先。
- 规则引擎负责证据校验、置信度、冲突、单轮限幅、跨会话重复、版本和审计。
- DeepSeek、Claude、GPT、GLM、Gemini 或 Kimi（均经 OpenRouter）负责从对话中生成结构化语义候选与回答策略，**不直接写入数据库**。
- Chatbot 应在自己的服务端分别调用本项目的画像 API 和语言模型 API：画像 API 维护状态并返回 `reply_hints`，语言模型 API 负责生成最终自然语言回复；两者不是同一个 API。
- 客户端不需要随消息上传整份画像。本服务按租户和 `user_id` 自行读取、版本化和保存画像；请求只携带消息、版本和必要的最近对话。
- 回复方式指令、短期状态、事实和事件不能修改长期性格；所有规则目标在编译与发布时都会和真实画像字段做闭环校验。
- 内置五份经授权的完整人物画像；其他人物可在工作台中新建。

## 项目结构

```text
profile-engine/
├── src/profile_engine/       # FastAPI 服务、画像引擎、规则与网页工作台
├── rules/                    # 四份规则源文档
├── source_profiles/          # 已授权的五份完整画像资料
├── migrations/               # Alembic 数据库迁移
├── scripts/                  # 演示与初始化脚本
├── tests/                    # API、规则、工作台和模型适配测试
├── Dockerfile                # 生产镜像
└── docker-compose.yml        # 本地 PostgreSQL 编排
```

## 快速开始

需要 Conda；项目统一使用仓库内的 Python 3.12 Conda 环境。

```bash
conda env create -p ./.conda-env -f environment.yml
conda run -p ./.conda-env pip install -e '.[dev]'
cp .env.example .env
conda run -p ./.conda-env alembic upgrade head
conda run --no-capture-output -p ./.conda-env profile-engine
```

也可以使用统一命令：

```bash
make install
cp .env.example .env
make migrate
make run
```

打开以下地址：

| 功能 | 地址 |
| --- | --- |
| 对话与画像工作台 | `http://localhost:8000/demo` |
| 规则管理工作台 | `http://localhost:8000/rules` |
| API 文档 | `http://localhost:8000/docs` |
| 健康检查 | `http://localhost:8000/health` |
| 存活/就绪检查 | `http://localhost:8000/livez`、`http://localhost:8000/readyz` |

运行测试：

```bash
make test
```

## 环境变量

复制 `.env.example` 后按实际环境配置。生产环境至少应设置：

```text
PROFILE_ENVIRONMENT=production
PROFILE_DATABASE_URL=postgresql+psycopg://...
PROFILE_TENANT_API_KEYS={"tenant-a":"独立长随机密钥"}
PROFILE_RULE_SOURCE_DIR=/rules
PROFILE_DEMO_ACCESS_CODE=为团队生成的访问密码
PROFILE_SEMANTIC_EXTRACTOR=model
PROFILE_DEFAULT_MODEL_PROVIDER=deepseek
PROFILE_OPENROUTER_API_KEY=OpenRouter服务密钥
PROFILE_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
PROFILE_DEEPSEEK_MODEL=deepseek/deepseek-v3.2
PROFILE_CLAUDE_MODEL=anthropic/claude-sonnet-5
PROFILE_GPT_MODEL=openai/gpt-5.6-sol
PROFILE_GLM_MODEL=z-ai/glm-5.2
PROFILE_GEMINI_MODEL=google/gemini-3.1-pro-preview
PROFILE_KIMI_MODEL=moonshotai/kimi-k3
PROFILE_ALLOW_EXTERNAL_SEMANTIC_PROCESSING=true
PROFILE_DEMO_FEATURES_ENABLED=false
PROFILE_API_DOCS_ENABLED=false
PROFILE_ALLOW_PROFILE_RESET=false
PROFILE_RATE_LIMIT_PER_MINUTE=120
```

所有可选模型均通过 OpenRouter 调用。工作台顶部可随时切换，模型共用服务器端 `PROFILE_OPENROUTER_API_KEY`，密钥不会下发浏览器。核心消息 API 可通过 `model_provider=deepseek|claude|gpt|glm|gemini|kimi` 按请求选择。若不使用外部模型服务，请将 `PROFILE_SEMANTIC_EXTRACTOR` 设为 `deterministic`。

## Docker 部署

本地使用 PostgreSQL 启动：

```bash
docker compose up --build
```

容器启动时会执行数据库迁移，然后启动 Web 服务。部署到托管平台时，保留 `PROFILE_DATABASE_URL`、访问密码和模型配置等环境变量，不要把 `.env` 上传到仓库或镜像中。

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/profiles:init` | 初始化人物画像 |
| `GET` | `/v1/profiles/{user_id}` | 读取当前画像和运行时状态 |
| `POST` | `/v1/profiles/{user_id}/messages:ingest` | 写入一轮消息并返回处理结果 |
| `GET` | `/v1/profiles/{user_id}/explain` | 查看证据、反证和版本历史 |
| `POST` | `/v1/profiles/{user_id}:correct` | 人工更正画像或事实 |
| `POST` | `/v1/profiles/{user_id}:set-enneagram` | 设置或替换已确认的九型人格结构 |
| `POST` | `/v1/profiles/{user_id}:forget` | 遗忘记忆、证据或关闭画像 |
| `GET` | `/v1/rule-packs/current` | 查看当前已发布规则包 |

写操作需要租户凭据、幂等键和正确的画像版本；版本冲突时服务返回 HTTP 409。

九型身份示例：

```json
{
  "expected_profile_version": 1,
  "enneagram": {
    "core_type": 7,
    "wing": 6,
    "primary_instinct": "SX",
    "secondary_instinct": "SO",
    "source": "expert_confirmed",
    "confidence": 0.95
  },
  "reason": "专家复核测评结果"
}
```

对话请求可在 `context.topic` 中提供 `career`、`learning`、`family`、`health` 等场景，系统会生成场景化互动策略，但不会修改九型身份。使用 `scope: "enneagram"` 可单独清除九型数据及其派生策略。

## 数据与隐私

- 人物画像、对话、事实、证据与审计属于敏感数据，应使用加密数据库、最小权限账号和备份策略。
- 人工更正和规则发布会写入审计记录，便于追溯。
- 如启用云端模型，原始对话会发送到配置的模型服务；请在产品侧完成用户授权、保留期限与删除流程设计。
- `source_profiles/` 中的资料仅应在已获授权的私有部署或私有仓库中使用。若要公开仓库，请先移除或替换为匿名示例数据。

## 开发说明

- 规则源文件位于 `rules/`，发布后会编译为带版本和校验结果的规则资产。
- `scripts/reset_demo_people.py` 会重建五位内置模板人物并保留其他演示人物；仅可用于明确授权的演示数据库。
- 开发时请保持 `.env`、数据库文件、模型密钥和任何未授权的个人资料不进入 Git。

## 许可证

当前项目为私有项目。对外分发、公开部署或使用其中的人物资料前，请先确认代码与数据的授权范围。
