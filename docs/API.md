# API 速查

> 完整 Swagger UI: http://localhost/docs
> 所有需要登录的端点都要带 `Authorization: Bearer <token>` 头

## Auth

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册（首注册自动 admin） |
| POST | `/api/auth/login` | 登录，返回 JWT |
| GET  | `/api/auth/me` | 当前用户 |

## Users (admin)

| Method | Path | 说明 |
|---|---|---|
| GET   | `/api/users` | 用户列表 |
| PATCH | `/api/users/{id}/active?active=bool` | 启用/禁用 |
| PATCH | `/api/users/{id}/admin?admin=bool` | 授予/取消管理员 |

## Projects

| Method | Path | 说明 |
|---|---|---|
| GET    | `/api/projects` | 我可访问的项目 |
| POST   | `/api/projects` | 创建项目 |
| GET    | `/api/projects/{id}` | 详情 |
| PATCH  | `/api/projects/{id}` | 更新 |
| DELETE | `/api/projects/{id}` | 删除（owner） |
| GET    | `/api/projects/{id}/members` | 成员列表 |
| POST   | `/api/projects/{id}/members` | 添加成员 |
| DELETE | `/api/projects/{id}/members/{user_id}` | 移除成员 |
| POST   | `/api/projects/{id}/source` | 上传 .txt 小说（multipart） |
| GET    | `/api/projects/{id}/episodes` | 项目下所有分集 |

## Tasks

| Method | Path | 说明 |
|---|---|---|
| GET   | `/api/tasks?project_id=&status=&assigned_to_me=` | 任务列表 |
| POST  | `/api/tasks` | 创建任务（手动触发流水线节点） |
| GET   | `/api/tasks/{id}` | 详情 |
| PATCH | `/api/tasks/{id}` | 更新（如指派） |
| POST  | `/api/tasks/{id}/retry` | 重试失败任务 |

## Episodes

| Method | Path | 说明 |
|---|---|---|
| GET    | `/api/episodes/{id}` | 单集详情 |
| GET    | `/api/episodes/{id}/scripts` | 文案历史版本 |
| GET    | `/api/episodes/{id}/storyboards` | 分镜列表 |
| POST   | `/api/episodes/{id}/storyboards` | 手动新增分镜 |
| PATCH  | `/api/episodes/storyboards/{sb_id}` | 修改分镜 |
| DELETE | `/api/episodes/storyboards/{sb_id}` | 删除分镜 |

## Reviews（人工在环）

| Method | Path | body |
|---|---|---|
| POST | `/api/reviews/tasks/{task_id}` | `{ "action": "approve\|rewrite\|enhance_conflict\|enhance_cliffhanger\|enhance_hook\|redo\|reject", "note": "..." }` |

approve 会自动派生流水线下一步任务（script→storyboard→video）。

## Workspace

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/workspace` | 聚合：我的项目 / 待我处理 / 待审核 / 待发布 |

## Assets / Accounts / Schedules

| Method | Path | 说明 |
|---|---|---|
| GET    | `/api/assets?project_id=` | 成片列表 |
| GET    | `/api/assets/{id}` | 成片详情 |
| GET    | `/api/accounts` | 我的发布账号 |
| POST   | `/api/accounts` | 新增账号 |
| DELETE | `/api/accounts/{id}` | 删除账号 |
| GET    | `/api/schedules?status=` | 排期列表 |
| POST   | `/api/schedules` | 新增排期 |
| PATCH  | `/api/schedules/{id}/status?status=published` | 更新发布状态 |
