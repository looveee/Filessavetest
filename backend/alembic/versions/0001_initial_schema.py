"""initial schema (15 tables)

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-09 00:00:00.000000

This migration represents the entire schema as it exists at v0.4.x.
Anyone bringing up a fresh database runs `alembic upgrade head`.
Anyone with an existing v0.4.x database (created via the now-removed
Base.metadata.create_all) runs `alembic stamp head` to mark the schema
as already up-to-date — see README §"Alembic 老库升级".

Schema invariants locked in by this migration:
  - audit_logs.project_id and audit_logs.task_id are PLAIN INTEGER, NOT
    foreign keys. Audit history must outlive its referents.
  - project_members has a unique (project_id, user_id) constraint.
  - Four PG enums: taskstatus / tasktype / projectrole / publishstatus.
  - ondelete semantics match SQLAlchemy models exactly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers
revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------
# Enum value tuples — keep in lock-step with app.models.* enums.
# Order matters for Postgres enum on-disk format; do not reorder casually.
# ---------------------------------------------------------------------
_TASK_STATUS = (
    "pending", "running", "waiting_human", "assigned", "in_review",
    "approved", "rejected", "revision_required", "failed", "completed",
)
_TASK_TYPE = (
    "outline_generation", "episode_split", "script_generation",
    "storyboard_generation", "video_generation", "editing",
    "review", "publish",
)
_PROJECT_ROLE = (
    "owner", "manager", "editor", "storyboarder",
    "reviewer", "publisher", "viewer",
)
_PUBLISH_STATUS = ("pending", "published", "failed", "skipped")


def _enum(name: str, values: tuple, create_type: bool = False) -> sa.Enum:
    """Helper that produces a column-level Enum referencing an already-
    created Postgres type. We create the types explicitly in upgrade()
    once, so individual columns must NOT try to create them again."""
    return postgresql.ENUM(*values, name=name, create_type=create_type)


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. Create Postgres enum types ----
    postgresql.ENUM(*_TASK_STATUS,    name="taskstatus").create(bind, checkfirst=True)
    postgresql.ENUM(*_TASK_TYPE,      name="tasktype").create(bind, checkfirst=True)
    postgresql.ENUM(*_PROJECT_ROLE,   name="projectrole").create(bind, checkfirst=True)
    postgresql.ENUM(*_PUBLISH_STATUS, name="publishstatus").create(bind, checkfirst=True)

    # ---- 2. users ----
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("is_admin", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"),       "users", ["id"],       unique=False)
    op.create_index(op.f("ix_users_email"),    "users", ["email"],    unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    # ---- 3. projects ----
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("genre", sa.String(length=64), nullable=True),
        sa.Column("style", sa.String(length=64), nullable=True),
        sa.Column("target_episodes", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="draft"),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_id"),       "projects", ["id"],       unique=False)
    op.create_index(op.f("ix_projects_owner_id"), "projects", ["owner_id"], unique=False)

    # ---- 4. project_members ----
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", _enum("projectrole", _PROJECT_ROLE),
                  nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"],    ["users.id"],    ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_user"),
    )
    op.create_index(op.f("ix_project_members_project_id"), "project_members", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_members_user_id"),    "project_members", ["user_id"],    unique=False)

    # ---- 5. source_texts ----
    op.create_table(
        "source_texts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"],  ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_source_texts_project_id"), "source_texts", ["project_id"], unique=False)

    # ---- 6. episodes ----
    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="draft"),
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("risk_flags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_episodes_project_id"), "episodes", ["project_id"], unique=False)

    # ---- 7. scripts ----
    op.create_table(
        "scripts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("cover_text", sa.String(length=255), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scripts_episode_id"), "scripts", ["episode_id"], unique=False)

    # ---- 8. storyboards ----
    op.create_table(
        "storyboards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("shot_number", sa.Integer(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=True, server_default="3.0"),
        sa.Column("visual", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("voiceover", sa.Text(), nullable=True),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("sfx", sa.String(length=255), nullable=True),
        sa.Column("bgm", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_storyboards_episode_id"), "storyboards", ["episode_id"], unique=False)

    # ---- 9. generation_tasks ----
    op.create_table(
        "generation_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("task_type", _enum("tasktype", _TASK_TYPE), nullable=False),
        sa.Column("status",    _enum("taskstatus", _TASK_STATUS),
                  nullable=False, server_default="pending"),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("risk_flags", sa.JSON(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"],  ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"],  ["episodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"],    ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"],  ["users.id"]),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_generation_tasks_id"),          "generation_tasks", ["id"],          unique=False)
    op.create_index(op.f("ix_generation_tasks_project_id"),  "generation_tasks", ["project_id"],  unique=False)
    op.create_index(op.f("ix_generation_tasks_episode_id"),  "generation_tasks", ["episode_id"],  unique=False)
    op.create_index(op.f("ix_generation_tasks_task_type"),   "generation_tasks", ["task_type"],   unique=False)
    op.create_index(op.f("ix_generation_tasks_status"),      "generation_tasks", ["status"],      unique=False)
    op.create_index(op.f("ix_generation_tasks_owner_id"),    "generation_tasks", ["owner_id"],    unique=False)
    op.create_index(op.f("ix_generation_tasks_assigned_to"), "generation_tasks", ["assigned_to"], unique=False)

    # ---- 10. task_assignments ----
    op.create_table(
        "task_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["generation_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_assignments_task_id"), "task_assignments", ["task_id"], unique=False)

    # ---- 11. assets ----
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("asset_type", sa.String(length=32), nullable=True, server_default="video"),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("cover_path", sa.String(length=512), nullable=True),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="ready"),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assets_project_id"), "assets", ["project_id"], unique=False)
    op.create_index(op.f("ix_assets_episode_id"), "assets", ["episode_id"], unique=False)

    # ---- 12. accounts ----
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("persona", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(length=64), nullable=True),
        sa.Column("publish_frequency", sa.String(length=64), nullable=True),
        sa.Column("credentials", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_accounts_owner_id"), "accounts", ["owner_id"], unique=False)

    # ---- 13. publish_schedules ----
    op.create_table(
        "publish_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("asset_id",   sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("status", _enum("publishstatus", _PUBLISH_STATUS),
                  nullable=False, server_default="pending"),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"],   ["assets.id"],   ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_publish_schedules_project_id"),   "publish_schedules", ["project_id"],   unique=False)
    op.create_index(op.f("ix_publish_schedules_asset_id"),     "publish_schedules", ["asset_id"],     unique=False)
    op.create_index(op.f("ix_publish_schedules_account_id"),   "publish_schedules", ["account_id"],   unique=False)
    op.create_index(op.f("ix_publish_schedules_scheduled_at"), "publish_schedules", ["scheduled_at"], unique=False)
    op.create_index(op.f("ix_publish_schedules_status"),       "publish_schedules", ["status"],       unique=False)

    # ---- 14. performance_metrics ----
    op.create_table(
        "performance_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=True),
        sa.Column("views",    sa.Integer(), nullable=True, server_default="0"),
        sa.Column("likes",    sa.Integer(), nullable=True, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("shares",   sa.Integer(), nullable=True, server_default="0"),
        sa.Column("completion_rate", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("collected_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["schedule_id"], ["publish_schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_performance_metrics_schedule_id"),
                    "performance_metrics", ["schedule_id"], unique=False)

    # ---- 15. audit_logs ----
    # NOTE: project_id and task_id are deliberately PLAIN INTEGER, not FKs.
    # This is the v0.3 fix that lets audit history outlive deleted projects
    # and tasks. The ondelete=SET NULL on user_id keeps the row alive when
    # a user is deleted but blanks their identity.
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("before_value", sa.JSON(), nullable=True),
        sa.Column("after_value", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_user_id"),    "audit_logs", ["user_id"],    unique=False)
    op.create_index(op.f("ix_audit_logs_project_id"), "audit_logs", ["project_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_task_id"),    "audit_logs", ["task_id"],    unique=False)
    op.create_index(op.f("ix_audit_logs_action"),     "audit_logs", ["action"],     unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)

    # ---- 16. notifications ----
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=512), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notifications_user_id"),    "notifications", ["user_id"],    unique=False)
    op.create_index(op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False)


def downgrade() -> None:
    """Drop everything in dependency-safe reverse order, then enums."""
    # tables (reverse of create order)
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_user_id"),    table_name="notifications")
    op.drop_table("notifications")

    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"),     table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_task_id"),    table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_project_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_user_id"),    table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_performance_metrics_schedule_id"), table_name="performance_metrics")
    op.drop_table("performance_metrics")

    for ix in ("status", "scheduled_at", "account_id", "asset_id", "project_id"):
        op.drop_index(op.f(f"ix_publish_schedules_{ix}"), table_name="publish_schedules")
    op.drop_table("publish_schedules")

    op.drop_index(op.f("ix_accounts_owner_id"), table_name="accounts")
    op.drop_table("accounts")

    op.drop_index(op.f("ix_assets_episode_id"), table_name="assets")
    op.drop_index(op.f("ix_assets_project_id"), table_name="assets")
    op.drop_table("assets")

    op.drop_index(op.f("ix_task_assignments_task_id"), table_name="task_assignments")
    op.drop_table("task_assignments")

    for ix in ("assigned_to", "owner_id", "status", "task_type",
               "episode_id", "project_id", "id"):
        op.drop_index(op.f(f"ix_generation_tasks_{ix}"), table_name="generation_tasks")
    op.drop_table("generation_tasks")

    op.drop_index(op.f("ix_storyboards_episode_id"), table_name="storyboards")
    op.drop_table("storyboards")

    op.drop_index(op.f("ix_scripts_episode_id"), table_name="scripts")
    op.drop_table("scripts")

    op.drop_index(op.f("ix_episodes_project_id"), table_name="episodes")
    op.drop_table("episodes")

    op.drop_index(op.f("ix_source_texts_project_id"), table_name="source_texts")
    op.drop_table("source_texts")

    op.drop_index(op.f("ix_project_members_user_id"),    table_name="project_members")
    op.drop_index(op.f("ix_project_members_project_id"), table_name="project_members")
    op.drop_table("project_members")

    op.drop_index(op.f("ix_projects_owner_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_id"),       table_name="projects")
    op.drop_table("projects")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_email"),    table_name="users")
    op.drop_index(op.f("ix_users_id"),       table_name="users")
    op.drop_table("users")

    # enums (after every table that referenced them is gone)
    bind = op.get_bind()
    postgresql.ENUM(name="publishstatus").drop(bind, checkfirst=True)
    postgresql.ENUM(name="projectrole").drop(bind, checkfirst=True)
    postgresql.ENUM(name="tasktype").drop(bind, checkfirst=True)
    postgresql.ENUM(name="taskstatus").drop(bind, checkfirst=True)
