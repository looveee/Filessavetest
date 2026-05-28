# AI 短视频半自动生产平台 / AI Short-Video Pipeline

> **当前版本: v0.6.1**
>
> 多用户 / 私有项目流 / 多人协作 / 显式权限矩阵 RBAC / **可配置可替换 AI Provider** / 人工在环审核 / 审计日志 / 多账号排期发布 / Redis 限流 / 生产启动安全校验 / Alembic 数据库迁移。

## v0.6.1 关键改动 — 真实 Provider 稳定性 & 可复现构建

v0.6.1 不引入新功能(不做视频生成 / TTS / WebSocket / 自动发布),聚焦**接入真实 AI Provider 后的稳定性、构建可复现、成本/超时/JSON 失败控制**:

- **前端构建可复现**:`frontend/package-lock.json` **现已提交**;Dockerfile 改用 `npm ci`(锁定版本精确安装);只有 `*.tsbuildinfo` 仍 gitignore。本地首装 `npm install`,Docker/CI/生产 `npm ci`。
- **Provider 验收脚本** `scripts/ai_provider_smoke.sh`:source `.env` → `GET /ai/providers` → admin 登录 → `POST /ai/test` → 建项目 → 建 `outline_generation` 任务 → 轮询完成 → 查 `ai_generation_runs` → 校验 provider/model 有记录、output 是 JSON、`error_message` 空、`response_hash` 存在、**不含 API Key**。mock 必通,真实 provider 同样可跑。
- **JSON 稳定性增强**:provider system prompt 显式要求"只输出 JSON、不要 markdown code fence";`json_repair` 能剥离 ```json``` 围栏、从前后夹杂文字中提取最外层 JSON 对象、容忍尾逗号;不可修复则任务 `failed`(并记 run 错误摘要,不落 prompt/Key)。
- **成本/超时/重试保护**:`AI_TIMEOUT_SECONDS` / `AI_MAX_OUTPUT_TOKENS` 生效;**5xx / 超时 / 网络错误**按 `AI_MAX_RETRIES` 重试,**4xx 不重试**直接 `failed`;新增**每用户每日软上限** `AI_DAILY_CALL_LIMIT_PER_USER`(默认 200),超限任务不调用 provider、直接 `failed` 并写审计。
- **Prompt seed 幂等**:`prompt_templates` 有 `(key, version)` 唯一约束;重复 `alembic upgrade head` 不重复插入(seed 按 `(key,version)` 跳过已存在行)。
- **任务状态 / 审计一致性**:AI 开始 `running` + `task.ai_started`;成功需人工审核 `waiting_human`、否则 `completed` + `task.ai_completed`;失败(含 repair 失败 / 超限)`failed` + `task.ai_failed`;`output_data` 始终是结构化 JSON。

## v0.6 关键改动 — AI Provider 接入

v0.5 之前 AI 是一个返回占位文本的 Mock。v0.6 把它升级成**可配置、可替换、可观测**的 Provider 架构,文本链路全部走真实结构化 JSON。

- **四种 Provider**:`mock`(本地确定性,无需网络/Key)、`claude`(Anthropic Messages API)、`openai`(OpenAI Chat Completions)、`openai_compatible`(Ollama / vLLM / LM Studio 等本地兼容服务)。通过 `AI_PROVIDER` 切换。
- **结构化输出**:`generate_outline` / `split_episodes` / `generate_script` / `generate_storyboard` / `generate_title_tags` / `review_content` / `rewrite_script` / `enhance_hook` / `enhance_conflict` / `enhance_cliffhanger` 全部返回经 Pydantic 校验的 JSON;parse 失败自动 repair 一次,仍失败则任务标记 `failed`。
- **Prompt 模板表** `prompt_templates`(Alembic 0002 创建并 seed 10 个默认模板):admin 可查看 / 编辑 / 克隆新版本;每次调用记录 `template key + version`。
- **AI 调用日志表** `ai_generation_runs`:每次调用一行(成功或失败),记录 provider / model / token / latency / 模板版本 / `request_hash` / `response_hash`。**永不记录 API Key,永不落库 prompt 明文**。
- **新 API**:`GET /api/ai/providers`、`POST /api/ai/test`(admin)、`GET /api/ai/runs`、`GET/PUT /api/prompts`、`POST /api/prompts/{id}/clone`(均 admin)。
- **前端**:新增 Settings(provider 状态 / 健康 / 测试 prompt)、Prompt Templates(列表 / 查看 / 编辑 / 克隆)页;项目详情任务卡展示 AI run 摘要;Dashboard 展示今日 AI 调用量。
- **生产校验**:`DEBUG=false` 且 `AI_PROVIDER!=mock` 时,启动会硬校验该 provider 所需配置是否齐全,缺失即拒绝启动。

> v0.6 只做文本链路。**不含**视频生成 / ComfyUI / 自动发布 / WebSocket(`video_generation` 仍是 mock 占位)。

详见 **第十七节 · AI Provider**。

## v0.5 关键改动 — Alembic 接管 schema

v0.4.x 一直用 `Base.metadata.create_all()` + 手写 `ALTER TABLE IF NOT EXISTS` 维护 schema,这是 MVP 妥协,不是工程做法。v0.5 把 schema 管理换成正式 Alembic:

- `backend/alembic.ini`、`backend/alembic/env.py`、`backend/alembic/versions/0001_initial_schema.py` 落地
- 容器 `entrypoint.sh` 在启动 uvicorn 前 `alembic upgrade head`(幂等)
- worker 容器**不**跑 migration,避免多容器抢跑
- `main.py` 里全部 `create_all` / `_DEV_MIGRATIONS` / `ALTER TABLE` 残留**已删除**
- `compare_type` / `compare_server_default` 启用,以后 `alembic revision --autogenerate` 能正确侦测列变更
- pytest 增加 3 个静态契约测试: 单一 head / main.py 不含 create_all / main.py 不含 ALTER TABLE

> **⚠ 老库升级必读**: 如果你现在跑的是 v0.4.x 数据库(create_all 创建的),**先备份**,再 `alembic stamp head`,**不要**直接 `upgrade head`(会撞重复创建)。详见第八节。

> **⚠ v0.5 仍是 MVP**。直接公网暴露需要先完成第七节"公网部署"全部 checklist。

---

## 一、技术栈

| 层 | 选型 |
|---|---|
| 前端 | Next.js 14 (App Router) + TypeScript + Tailwind |
| 后端 | FastAPI + SQLAlchemy 2.0 + Pydantic v2 |
| 数据库 | PostgreSQL 15 + Alembic 1.13 |
| AI | **可插拔 Provider**:mock / Claude / OpenAI / OpenAI 兼容(httpx) |
| 队列 | Redis 7 + Celery 5 |
| 限流 | Redis sliding-window counter |
| 反向代理 | Nginx |
| 部署 | Docker Compose(开发版 + 生产版) |

---

## 二、目录结构

```
ai-shortvideo-platform/
├── backend/
│   ├── alembic.ini              # ★ v0.5 Alembic 入口
│   ├── alembic/
│   │   ├── env.py               # ★ 从 settings 读 DATABASE_URL,加载 Base.metadata
│   │   ├── script.py.mako       # ★ revision 模板
│   │   └── versions/
│   │       └── 0001_initial_schema.py    # ★ 初始 15 表 + 4 enum
│   ├── entrypoint.sh            # ★ alembic upgrade head + uvicorn
│   ├── app/
│   │   ├── main.py              # ★ 已清理:不再 create_all
│   │   ├── config.py
│   │   ├── startup_check.py     # 生产安全校验
│   │   ├── database.py
│   │   ├── security.py
│   │   ├── deps.py
│   │   ├── permissions.py
│   │   ├── celery_app.py
│   │   ├── models/__init__.py   # 15 张表
│   │   ├── schemas/__init__.py
│   │   ├── services/{ai_service,audit,ratelimit}.py
│   │   ├── tasks/ai_tasks.py
│   │   └── api/                 # auth/users/projects/tasks/episodes/reviews/
│   │                            # workspace/assets_accounts/audit_logs/system
│   ├── tests/                   # 5 个测试文件
│   │   └── test_alembic_state.py    # ★ 单一 head + main.py 无 create_all
│   ├── pytest.ini
│   ├── Dockerfile               # ★ COPY alembic + entrypoint.sh
│   └── requirements.txt
├── frontend/                    # Next.js 11 个页面
├── nginx/{nginx.conf, nginx.prod.conf}
├── scripts/smoke_test.sh        # ★ 启动时检查 alembic + main.py 干净
├── docs/API.md
├── docker-compose.yml           # ★ backend 用 entrypoint.sh
├── docker-compose.prod.yml      # ★ backend 用 entrypoint.sh
├── .env.example
└── README.md
```

---

## 三、本地开发启动

```bash
cd ai-shortvideo-platform
cp .env.example .env
docker-compose up -d --build
sleep 15
bash scripts/smoke_test.sh
```

启动时 backend 会先跑 `alembic upgrade head`(全新库会执行 0001_initial_schema 创建 15 表 + 4 enum),然后才起 uvicorn。

| 入口 | URL |
|---|---|
| 前端 | http://localhost |
| API 文档 | http://localhost/docs |
| 直连后端 | http://localhost:8000/docs |
| 直连前端 | http://localhost:3000 |

### 前端依赖与可复现构建(v0.6.1)

`frontend/package-lock.json` **是受版本管理的文件,必须提交**。它锁定整棵依赖树,保证 Docker/CI/生产构建出的产物与本地一致。

| 场景 | 命令 | 说明 |
|---|---|---|
| 本地首次安装 / 增删依赖 | `npm install` | 会按 `package.json` 解析并**更新 `package-lock.json`**,记得把变更一起提交 |
| Docker / CI / 生产构建 | `npm ci` | 严格依据 `package-lock.json` 精确安装;锁文件与 `package.json` 不一致会**直接报错**(这正是我们想要的) |

`frontend/Dockerfile` 的 deps 阶段已改为 `COPY package.json package-lock.json ./ && npm ci`。唯一仍被 gitignore 的前端产物是 TypeScript 增量缓存 `*.tsbuildinfo`。

> 改完依赖后若忘记提交新的 `package-lock.json`,`npm ci` 会在 CI 阶段失败并提示 lock 与 manifest 不一致——按提示本地 `npm install` 再提交锁文件即可。

---

## 四、生产启动

**生产 .env 必填**:

```bash
DEBUG=false                        # 触发 startup_check
SERVE_STORAGE_DIRECT=false         # 关 /storage 直挂
SECRET_KEY=<openssl rand -base64 64>
CORS_ORIGINS=https://your.domain
POSTGRES_PASSWORD=<强密码>
```

```bash
cp .env.example .env
vim .env

docker-compose -f docker-compose.prod.yml up -d --build

# 看 backend 启动日志,前几行应该是 `>> entrypoint: applying migrations`
docker-compose -f docker-compose.prod.yml logs backend | head -20

# 确认仅 nginx 80 暴露
ss -tlnp | grep -E ':(8000|3000|5432|6379)'   # 应为空
curl -s http://localhost/api/system/health
```

**生产升级前必备份数据库**:
```bash
docker-compose -f docker-compose.prod.yml exec db \
  pg_dump -U postgres shortvideo > backup_$(date -u +%Y%m%dT%H%M%SZ).sql
```

如果 migration 失败,backend 容器会 `set -e` 退出,**不会带病启动 API**。

---

## 五、Alembic 操作手册

所有命令都在 backend 容器里跑,这样 `DATABASE_URL` / Python 依赖都是对的。

### 创建新 migration(autogenerate)

改了 `app/models/__init__.py` 之后:

```bash
docker-compose exec backend \
  alembic revision --autogenerate -m "add account_metrics table"
```

生成的文件落在 `backend/alembic/versions/<rev>_add_account_metrics_table.py`,**手工 review** 一遍(autogenerate 不是万能,enum 增减、约束改动经常需要手工修),然后 commit。

### 应用 migration

```bash
docker-compose exec backend alembic upgrade head
```

也可以升到指定 revision: `alembic upgrade <rev>`。

容器重启时 `entrypoint.sh` 也会自动跑这条命令(idempotent 安全)。

### 查看当前版本

```bash
docker-compose exec backend alembic current
# 0001_initial (head)
```

### 标记已有数据库为已迁移(老库迁过来用)

**只在 schema 已经手工对齐了的库上用!** 不是迁移,只是写 `alembic_version` 表:

```bash
docker-compose exec backend alembic stamp head
```

### 回滚一个版本

```bash
docker-compose exec backend alembic downgrade -1
```

或回到具体 revision: `alembic downgrade <rev>`,`alembic downgrade base` 一路退回空库。

### 看历史

```bash
docker-compose exec backend alembic history --verbose
```

### 离线生成 SQL(不连库)

```bash
docker-compose exec backend alembic upgrade head --sql > migration.sql
```

适合给 DBA 走人工审批流程的环境。

---

## 六、entrypoint 设计说明

为什么是 backend 容器在 entrypoint 里直接跑 `alembic upgrade head`,**没有**单独建 `migrate` 一次性服务?

- `alembic upgrade head` 在已是 head 时**完全幂等** — 它只会 `SELECT version_num FROM alembic_version` 然后立即返回。重启 backend 不会反复迁移。
- worker 不跑 migration,所以多副本 backend 同时启动时**只有第一个抢到 alembic 锁**(Postgres 的 `CREATE TABLE` / DDL 在事务内串行,后启动的 backend 看到 head 已是最新会立即跳过),不会损坏 schema。
- 引入额外 `migrate` 服务只增加 compose 编排复杂度,对单机部署没收益。多副本 / 多节点部署时再考虑专用 `migrate` job。

如果以后你要换成"专用 migrate job"模式,改法是:
1. 加一个 `migrate` 服务,`command: ["alembic", "upgrade", "head"]`,`restart: "no"`
2. 把 backend 的 `command:` 改回 `["uvicorn", ...]` 并 `depends_on: { migrate: { condition: service_completed_successfully } }`

---

## 七、端口安全

| 端口 | dev | prod |
|---|---|---|
| 80 (nginx) | 暴露 | 暴露 |
| 443 (nginx) | — | 推荐 |
| 8000 (backend) | 暴露调试 | **不暴露** |
| 3000 (frontend) | 暴露调试 | **不暴露** |
| 5432 (postgres) | 不暴露 | **不暴露** |
| 6379 (redis) | 不暴露 | **不暴露** |

---

## 八、从 v0.4.x 升级到 v0.5.0(老库迁移)

**v0.4.x 用 `create_all` 创的库,数据库里没有 `alembic_version` 表。直接跑 v0.5 的 entrypoint 会失败**(尝试 `CREATE TABLE users` 撞 already exists)。

正确步骤:

```bash
# 1. 备份(必须)
docker-compose exec db pg_dump -U postgres shortvideo > pre_v05_backup.sql

# 2. 先停 backend / worker,db / redis 不动
docker-compose stop backend worker

# 3. 拉 v0.5 代码,build 镜像
git pull
docker-compose build backend

# 4. 一次性进 backend 容器,把现有 schema 标为 head
docker-compose run --rm --no-deps --entrypoint="" backend alembic stamp head

# 5. 正常启动 — 从此 alembic upgrade head 是 no-op,后续新增 revision 走正常流程
docker-compose up -d backend worker
```

第 4 步的 `--entrypoint=""` 是关键:跳过 entrypoint.sh 的 `alembic upgrade head`,只跑 `alembic stamp head`,因为现在的库 schema 已经是 head 内容(只是没标记)。

> **不要为了兼容老库在 0001_initial 里塞 `IF NOT EXISTS`** — 正式迁移要保持干净,兼容性走 `stamp` 流程。

---

## 九、权限模型

> v0.3+ 用显式权限矩阵 `require_permission(db, user, project_id, Permission.X)`,不再有权限等级数字。

### 全局
- **admin**: 隐式拥有所有项目的所有权限

### 项目级 21 个权限点

```
project.read / project.update / project.delete
member.manage
source.upload
episode.read
script.create / script.update
storyboard.create / storyboard.update
task.create / task.assign / task.retry
review.approve / review.reject / review.rewrite
asset.read
schedule.create / schedule.update
account.create / account.use
```

### 7 个角色

| 角色 | 权限数 | 关键能力 |
|---|---|---|
| owner | 21 | 全部(只能是项目创建者) |
| manager | 20 | 全部 except `project.delete` |
| editor | 10 | 文案 / 分镜 / 任务 / 上传小说 |
| storyboarder | 7 | 分镜 + 任务,不能改文案 |
| reviewer | 6 | review.* + 读,不能 task.create |
| publisher | 6 | account.* + schedule.*,不能改文案/分镜 |
| viewer | 3 | 只读 |

> 完整矩阵: `GET /api/system/permission-matrix` (admin only)

---

## 十、审计日志

`app.services.audit.log_audit()` 写 `audit_logs` 表。
字段: `user_id, project_id, task_id, action, target_type, target_id, before_value, after_value, ip, created_at`。

> **`project_id` / `task_id` 不是外键** — 删除项目/任务后审计行存活。0001_initial migration 里就是这么写的。

查询: `GET /api/audit-logs`,行级隔离 + 筛选 `action / project_id / user_id / since / until`。前端: `/audit`。

---

## 十一、限流(Redis)

| 端点 | 默认 | 计数语义 |
|---|---|---|
| POST /api/auth/login | 10/min/IP | 每次都计数 |
| POST /api/auth/login | 10/5min/username | **仅失败计数(v0.4.1)** |
| POST /api/auth/register | 10/hour/IP | 每次都计数 |
| GET /api/users/lookup | 30/min/IP | 每次都计数 |

429 + `Retry-After`,审计 `auth.rate_limited` / `user.lookup_rate_limited`,登录错误统一 "Invalid credentials"。Redis 不可用时 fail-open。

---

## 十二、私有资产访问

- dev: `SERVE_STORAGE_DIRECT=true`,nginx + backend 都直挂 `/storage/`
- prod: `SERVE_STORAGE_DIRECT=false`,只能 `GET /api/assets/{id}/download` 鉴权下载
- AssetOut 不返回 `file_path`,只有 `download_url`;调试看 `GET /api/assets/{id}/debug` (admin only)

---

## 十三、API 速查

> dev: http://localhost/docs;prod: 默认 404(在 nginx.prod.conf 里按需开启)

### Auth / Users
- POST `/api/auth/{register,login}` — 限流
- GET `/api/auth/me`
- GET `/api/users` — admin 全部 / 普通用户只看自己
- GET `/api/users/lookup?username=X` — 限流,无 email
- GET `/api/users/{id}`,PATCH `/api/users/{id}/{active,admin}` — admin

### Projects / Tasks / Episodes / Reviews
照旧。Reviews 7 种 action: `approve / reject / rewrite / enhance_conflict / enhance_cliffhanger / enhance_hook / redo`。

### Assets / Accounts / Schedules
- GET `/api/assets`、`/api/assets/{id}` — 脱敏
- GET `/api/assets/{id}/debug` — admin only
- GET `/api/assets/{id}/download` — 鉴权下载
- GET / POST / DELETE `/api/accounts`
- GET `/api/accounts/usable?project_id=X`
- GET / POST `/api/schedules`,PATCH `/api/schedules/{id}/status`

### Workspace / Audit / System
- GET `/api/workspace` — 含 `summary.ai_calls_today` / `ai_tokens_today`
- GET `/api/audit-logs` — 行级隔离
- GET `/api/system/health` — 公开,只 `{ok, version, app_name}`
- GET `/api/system/health-full` — admin only,含 `ai` provider 状态(无密钥)
- GET `/api/system/permission-matrix` — admin only

### AI / Prompts (v0.6)
- GET `/api/ai/providers` — 任意已登录用户;当前 provider/model + 各 provider 是否已配置(**不含密钥**)
- POST `/api/ai/test` — admin;freeform prompt → `{provider, model, latency_ms, tokens, ok, sample_output}`
- GET `/api/ai/runs?project_id=&task_id=&status=` — 行级隔离(admin 全部 / owner+member 看自己项目 / 自己触发的)
- GET `/api/prompts`、GET `/api/prompts/{id}` — admin
- PUT `/api/prompts/{id}` — admin,编辑(`name / system_prompt / user_prompt_template / output_schema / is_active`)
- POST `/api/prompts/{id}/clone` — admin,克隆为同 key 的新 version(默认未激活)

---

## 十四、数据库

15 张表(由 `0001_initial_schema.py` 创建):
`users, projects, project_members, source_texts, episodes, scripts, storyboards, generation_tasks, task_assignments, assets, accounts, publish_schedules, performance_metrics, audit_logs, notifications`

v0.6 新增 2 张表(`0002_ai_provider.py`):`prompt_templates`(并 seed 10 个默认模板)、`ai_generation_runs`。后者的 `project_id / task_id / episode_id` 与 `audit_logs` 一样是**纯 INTEGER 非外键**,使遥测能在被引用行删除后存活。

4 个 PG 枚举: `taskstatus / tasktype / projectrole / publishstatus`。

```bash
# 备份
docker-compose exec db pg_dump -U postgres shortvideo > backup_$(date +%F).sql

# 恢复
docker-compose exec -T db psql -U postgres shortvideo < backup_2026-01-15.sql
```

---

## 十五、测试

```bash
# 静态契约 (无依赖) — 最常跑的一组
DATABASE_URL='postgresql+psycopg2://x:x@nowhere/x' SECRET_KEY=test \
  pytest backend/tests/test_permission_matrix.py backend/tests/test_alembic_state.py -v
# 16 perm + 4 alembic-state(1 skipped: live alembic)

# AI provider 纯逻辑 (无依赖):mock 结构化输出 / JSON repair(code fence /
# 夹杂文字 / 尾逗号 / 失败)/ HTTP 重试策略(5xx 重试、4xx 不重试、超时)/
# 每日调用上限
DATABASE_URL='postgresql+psycopg2://x:x@nowhere/x' SECRET_KEY=test \
  pytest backend/tests/test_ai_provider.py -v   # DB/API 用例自动 skip

# 集成测试 (需 db + redis) — 含 ai_generation_runs / prompt RBAC / ai/test admin
# 门禁 / ai/runs 行级隔离(test_ai_runs_security.py)
docker-compose up -d db redis
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/shortvideo \
REDIS_URL=redis://localhost:6379/15 \
SECRET_KEY=test \
  pytest backend/tests/ -v

# 启用 live alembic upgrade head 测试 (一次性扔库)
createdb shortvideo_alembic_test
ALEMBIC_LIVE_TEST_DB_URL=postgresql+psycopg2://localhost/shortvideo_alembic_test \
  pytest backend/tests/test_alembic_state.py::test_alembic_upgrade_head_live -v

# 端到端 smoke(RBAC / 审计 / 上传安全等)
docker-compose up -d --build
sleep 15
bash scripts/smoke_test.sh

# AI Provider 验收 smoke(v0.6.1):整条文本链路 + run 落库安全校验
#   source .env 决定用哪个 provider(mock 必通,真实 provider 同样可跑)
API_BASE=http://localhost:8000 bash scripts/ai_provider_smoke.sh
```

---

## 十六、常见问题

**Q: backend 启动报 `relation "users" already exists`?**
A: 你是 v0.4.x 老库,跳过了第八节的 `alembic stamp head`。先备份,然后:
```bash
docker-compose run --rm --no-deps --entrypoint="" backend alembic stamp head
docker-compose up -d backend worker
```

**Q: backend 启动报 `Multiple head revisions are present`?**
A: migration 树分叉了。`alembic heads` 看一下,merge:
```bash
docker-compose exec backend alembic merge -m "merge heads" <rev1> <rev2>
```

**Q: 我新加了一张表,autogenerate 没生成?**
A: 确认 import 链能加载到那张表:`backend/alembic/env.py` 末尾 `import app.models` 触发 `app/models/__init__.py`,你的新表 class 必须在那里能被 import 到(直接定义在文件里,或者 `from .new_table import NewTable`)。

**Q: 生产升级想滚动重启,会重复 migrate 吗?**
A: 不会。`alembic upgrade head` 在 head 已是最新时是 noop。但**多副本同时启动**时,理论上有竞态(两个 backend 都看到 head 不存在,都尝试 CREATE TABLE);Postgres 的 DDL 锁会让第二个失败,然后 `set -e` 让它退出再被 docker 拉起,这时它看到 head 已是最新,正常起来。可控,但不优雅。要彻底干净,见第六节末尾的"专用 migrate job"做法。

**Q: pytest 跑 `test_alembic_upgrade_head_live` 怎么启用?**
A: 设环境变量 `ALEMBIC_LIVE_TEST_DB_URL=postgresql://...` 指向一个**空的扔库**。我们故意没在默认 fixture 里启用,因为大多数开发机不想为静态测试启动 PG 实例。

---

## 十七、AI Provider(v0.6)

### 1. 切换 Provider

通过 `.env` 的 `AI_PROVIDER` 切换,改完重启 backend + worker:

```bash
AI_PROVIDER=mock              # 默认:本地确定性内容,无需网络/Key
AI_PROVIDER=claude            # Anthropic Messages API
AI_PROVIDER=openai            # OpenAI Chat Completions
AI_PROVIDER=openai_compatible # 本地 Ollama / vLLM / LM Studio
```

通用旋钮(作为各 provider 的 fallback):`AI_MODEL / AI_BASE_URL / AI_API_KEY / AI_TIMEOUT_SECONDS / AI_MAX_RETRIES / AI_TEMPERATURE / AI_MAX_OUTPUT_TOKENS`。

### 2. mock 模式

无需任何配置。所有 smoke / 集成测试默认在 mock 下跑;输出基于输入哈希确定性生成,且严格符合各操作的 Pydantic schema。

> 通用旋钮里 `AI_TIMEOUT_SECONDS` / `AI_MAX_OUTPUT_TOKENS` 对所有真实 provider 生效;`AI_MAX_RETRIES` 只对 **5xx / 超时 / 网络错误**生效,**4xx(401/403/404/400 等)永不重试**,直接判失败。

### 3. 真实 Provider 配置样例

**Claude(Anthropic Messages API)**

```bash
AI_PROVIDER=claude
CLAUDE_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-6        # 或其它可用模型
# 可选 AI_BASE_URL 走自建网关,默认 https://api.anthropic.com
```

**OpenAI(Chat Completions)**

```bash
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o                    # 或 gpt-4o-mini 等
# 可选 OPENAI_BASE_URL,默认 https://api.openai.com/v1
# OpenAI 走 response_format=json_object,系统提示也再次要求只输出 JSON
```

**Ollama(本地)**

```bash
AI_PROVIDER=openai_compatible
LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1
LOCAL_LLM_MODEL=qwen2.5:7b            # 需先 `ollama pull qwen2.5:7b`
# Ollama 不需要 Key;留空即可
```

**vLLM(本地 / 自托管,OpenAI 兼容)**

```bash
AI_PROVIDER=openai_compatible
LOCAL_LLM_BASE_URL=http://host.docker.internal:8000/v1
LOCAL_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct   # 与 vllm serve 的 --served-model-name 一致
OPENAI_API_KEY=any-nonempty-token          # vLLM 若启用 --api-key 则必须非空
```

**LM Studio(本地,OpenAI 兼容)**

```bash
AI_PROVIDER=openai_compatible
LOCAL_LLM_BASE_URL=http://host.docker.internal:1234/v1   # LM Studio 默认端口
LOCAL_LLM_MODEL=lmstudio-community/qwen2.5-7b-instruct    # 用 LM Studio 里显示的 model id
# LM Studio 通常不校验 Key;留空即可
```

> 在 Docker 里连宿主机的本地模型,用 `host.docker.internal`(Linux 需在 compose 给容器加 `extra_hosts: ["host.docker.internal:host-gateway"]`,或直接填宿主机 IP)。

### 4. 验收脚本:分别测 mock / Claude / OpenAI / 本地模型

`scripts/ai_provider_smoke.sh` 跑通整条文本链路并校验 run 落库安全。它会 `source .env`,所以**用哪个 provider 取决于 `.env` 里的 `AI_PROVIDER`**:

```bash
# mock(无需任何配置,CI 必跑)
echo "AI_PROVIDER=mock" > .env
API_BASE=http://localhost:8000 bash scripts/ai_provider_smoke.sh

# Claude:把 .env 配成上面的 Claude 样例,然后
API_BASE=http://localhost:8000 bash scripts/ai_provider_smoke.sh

# OpenAI / Ollama / vLLM / LM Studio:同理,改 .env 后重跑
# (改 .env 后记得重启 backend + worker,让新配置生效)

# DB 非空时第一个注册用户不是 admin,传一个已有 admin token:
ADMIN_TOKEN=<token> API_BASE=http://localhost:8000 bash scripts/ai_provider_smoke.sh
```

脚本断言:provider/model 有记录、`output_data` 是 JSON 对象、run `error_message` 为空、`response_hash`/`request_hash` 存在、`/ai/providers`+`/ai/test`+`/ai/runs` 响应里**都不含 API Key**、run 不含 prompt/raw_response。

### 5. API Key 安全

- Key 只存在于 backend 环境变量,**永不下发前端**。
- `GET /api/ai/providers` 只返回 `configured: true/false` 和缺失字段**名**,绝不返回 Key 值。
- `ai_generation_runs` 不存 Key、不存 prompt 明文,只存 `request_hash` / `response_hash`(sha256);`GET /api/ai/runs` 也不返回 prompt / raw_response。
- `DEBUG=false` 且 `AI_PROVIDER!=mock` 时,启动会校验该 provider 必填项是否齐全,缺失即拒绝启动。

### 6. 成本 / 超时 / 每日上限

| 旋钮 | 作用 |
|---|---|
| `AI_TIMEOUT_SECONDS` | 单次上游请求超时;超时后任务 `failed` 并写 run |
| `AI_MAX_RETRIES` | 仅对 5xx / 超时 / 网络错误重试;4xx 不重试 |
| `AI_MAX_OUTPUT_TOKENS` | 模型输出上限(`max_tokens` / `max_output_tokens`) |
| `AI_DAILY_CALL_LIMIT_PER_USER` | 每用户每 UTC 日 AI 调用软上限(默认 200,`<=0` 关闭) |

超过每日上限时,任务**不调用 provider**、直接标记 `failed` 并写 `task.ai_failed`(`reason=daily_call_limit`)审计。

### 7. Prompt 模板管理(克隆 / 启用 / 回滚)

- 10 个默认模板由迁移 0002 seed;代码默认值在 `backend/app/services/ai/default_prompts.py`(DB 无对应行时回退到这里)。表上有 `(key, version)` 唯一约束,重复 `alembic upgrade head` 不会重复插入。
- 运行时按 key 取**最高版本的 active 行**;改了 active 模板,下一次 AI 调用立即生效。
- **克隆新版本**:`POST /api/prompts/{id}/clone` —— 基于某行复制出同 key 的 `version+1`,默认 `is_active=false`。
- **启用新版本**:验证后 `PUT /api/prompts/{newId}` 设 `{"is_active": true}`;同 key 取最高版本的 active 行,因此新版本即刻接管。
- **回滚模板**:把出问题的版本 `PUT {"is_active": false}`,并把要回退到的旧版本 `PUT {"is_active": true}`(无需删除任何行,版本历史保留)。

### 8. AI 调用日志

- 每次调用(成功或失败)写一行 `ai_generation_runs`:provider / model / token / latency / 模板 key+version / 状态 / 错误。
- 前端项目详情「任务」页每个任务卡展示对应 run 摘要;Dashboard 顶部展示今日 AI 调用量。
- `GET /api/ai/runs` 行级隔离:admin 看全部;项目 owner / 成员只看被授权项目;普通用户**不能**用 `?project_id=` 枚举别人项目的 run(返回 403)。

### 9. 常见错误

| 现象 | 原因 / 处理 |
|---|---|
| `missing required config: CLAUDE_API_KEY ...` 启动失败 | 选了非 mock provider 但没配齐 Key/Model;补 `.env` 后重启 |
| `model not found` / `provider error: ... 404 ... model` | `*_MODEL` 名称错误、未 `ollama pull`、或该账号无权访问;核对模型 id |
| `provider error: ... timeout` | 上游超时;调大 `AI_TIMEOUT_SECONDS`,或本地模型太慢/未加载 |
| 任务 `failed`,error 含 `schema validation failed` | 模型输出不符合 JSON schema;已自动 repair 一次仍失败 → 调整该操作的 prompt 模板 |
| 任务 `failed`,error 含 `json parse failed after repair` | 模型返回的根本不是 JSON(invalid json);强化模板"只输出 JSON、无 code fence"约束,或换更听话的模型 |
| `request error 401` / `403`(4xx,**不重试**) | Key 失效 / 无权限 / 余额不足;换 Key 或检查账号,改完重启 |
| 本地模型连不上(`connection error` / `Connection refused`) | `LOCAL_LLM_BASE_URL` 不可达;确认本地服务在跑、端口对、Docker 网络能到宿主机 |
| `request error 400 ... maximum context length` / token limit exceeded | 输入+输出超模型上限;减小 `AI_MAX_OUTPUT_TOKENS` 或缩短输入/模板 |
| 任务 `failed`,error 含 `daily AI call limit exceeded` | 该用户当日调用数超 `AI_DAILY_CALL_LIMIT_PER_USER`;次日重置,或调大该值 |

---

## License

内部使用 / Demo。生产部署请按上文 checklist 加固。
