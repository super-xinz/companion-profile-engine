# 模板人物安全播种指南

`scripts/seed_template_people.py` 用于把 5 份已授权的完整人物画像补充到一个**明确指定**的租户。它默认只做只读检查，不会建表、发布规则、重置人物、删除数据或修改已有记录。

## 默认播种对象

对外使用不含生日的安全 ID 和中性名称；生日只作为服务端导入对应授权画像的映射条件。

| 对外 `tenant_user_id` | 展示名称 | 服务端资料映射 |
| --- | --- | --- |
| `showcase-explorer` | 灵感探索者 | `1988-08-09` |
| `showcase-innovator` | 观点开拓者 | `1989-10-15` |
| `showcase-strategist` | 果断策略者 | `1989-11-28` |
| `showcase-supporter` | 温暖协调者 | `1996-03-28` |
| `showcase-anchor` | 稳健守护者 | `1998-12-06` |

新建画像内的 `identity.template_person_id` 同样使用对应的 `showcase-*` ID，不会把旧 `person-日期` ID写入新画像。

## 运行前检查

1. 确认目标数据库已经执行 Alembic 迁移，并且至少存在一个 `published` 状态的规则包。
2. 确认运行环境已通过平台 Secret 注入正确的 `PROFILE_DATABASE_URL`；不要把数据库密码写进命令、代码或 Git。
3. 确认容器包含 `source_profiles/` 下的 5 个授权工作簿。
4. 先备份生产数据库，并再次核对目标 `tenant_id`。脚本不会读取默认租户，`--tenant` 必须显式填写。

## 第一步：只读预演

在项目根目录或部署容器的 `/app` 目录执行：

```bash
python scripts/seed_template_people.py --tenant robot-company-prod
```

默认输出 JSON 计划：

- `mode: "dry_run"`：没有执行写入；
- `would_create`：该 ID 不存在，应用时会新建；
- `skip_existing`：该 ID 已存在，应用时会整条跳过；
- `create_count` 与 `skip_count` 的合计应为 5。

只预演某一个安全示例：

```bash
python scripts/seed_template_people.py \
  --tenant robot-company-prod \
  --person showcase-anchor
```

## 第二步：正式应用

确认预演中的数据库环境、租户和数量都正确后，额外提供 `--apply`：

```bash
python scripts/seed_template_people.py \
  --tenant robot-company-prod \
  --apply
```

正式输出中：

- `created` 表示刚刚创建；
- `skipped_existing` 表示运行前已存在，或并发运行时被另一进程先创建；
- 已存在的人物不会被改名，不会覆盖生日、授权、画像版本、证据或任何其他字段；
- 中途失败后可以原命令重跑，已成功的人物会被跳过，只继续补剩余人物。

应用完成后再次执行不带 `--apply` 的预演命令。正常结果应为 `create_count: 0`、`skip_count: 5`。

## 旧 ID 兼容模式

只有确实需要兼容旧工作台数据时，才显式添加 `--legacy`。该模式处理原来的 5 个 `person-*` ID；默认模式永远不会选择它们。

```bash
# 先只读预演旧 ID；不要直接应用
python scripts/seed_template_people.py \
  --tenant robot-company-prod \
  --legacy
```

旧 ID 与新的 `showcase-*` ID 是不同数据库记录。不要在没有迁移方案时同时播种两套，否则同一份资料会出现两个人物入口。

## 禁止用于生产的旧脚本

不要在生产数据库运行 `scripts/reset_demo_people.py`。该脚本会删除并重建旧模板人物，属于破坏性演示维护工具；本指南中的安全播种脚本只插入缺失项。

