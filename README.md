# AI 短视频半自动生产平台 / AI Short-Video Pipeline

> **当前版本: v0.5.0**
>
> 多用户 / 私有项目流 / 多人协作 / 显式权限矩阵 RBAC / AI 生成 / 人工在环审核 / 审计日志 / 多账号排期发布 / Redis 限流 / 生产启动安全校验 / **Alembic 数据库迁移**。

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
| 数据库 | PostgreSQL 15 + **Alembic 1.13** |
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
- GET `/api/workspace`
- GET `/api/audit-logs` — 行级隔离
- GET `/api/system/health` — 公开,只 `{ok, version, app_name}`
- GET `/api/system/health-full` — admin only
- GET `/api/system/permission-matrix` — admin only

---

## 十四、数据库

15 张表(由 `0001_initial_schema.py` 创建):
`users, projects, project_members, source_texts, episodes, scripts, storyboards, generation_tasks, task_assignments, assets, accounts, publish_schedules, performance_metrics, audit_logs, notifications`

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
# 19 passed (16 perm + 3 alembic-state),1 skipped (live alembic)

# 集成测试 (需 db + redis)
docker-compose up -d db redis
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/shortvideo \
REDIS_URL=redis://localhost:6379/15 \
SECRET_KEY=test \
  pytest backend/tests/ -v

# 启用 live alembic upgrade head 测试 (一次性扔库)
createdb shortvideo_alembic_test
ALEMBIC_LIVE_TEST_DB_URL=postgresql+psycopg2://localhost/shortvideo_alembic_test \
  pytest backend/tests/test_alembic_state.py::test_alembic_upgrade_head_live -v

# 端到端 smoke
docker-compose up -d --build
sleep 15
bash scripts/smoke_test.sh
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

## License

内部使用 / Demo。生产部署请按上文 checklist 加固。
