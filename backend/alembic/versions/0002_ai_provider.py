"""ai provider: prompt_templates + ai_generation_runs (v0.6)

Revision ID: 0002_ai_provider
Revises: 0001_initial
Create Date: 2026-05-28 00:00:00.000000

Adds the two v0.6 tables and seeds the default prompt templates from
app.services.ai.default_prompts so a fresh `alembic upgrade head` gives a
ready-to-use prompt library. The seed is idempotent-ish: it only inserts
rows for keys that are not already present.
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_ai_provider"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- prompt_templates ----
    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt_template", sa.Text(), nullable=True),
        sa.Column("output_schema", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "version", name="uq_prompt_key_version"),
    )
    op.create_index(op.f("ix_prompt_templates_key"), "prompt_templates", ["key"], unique=False)
    op.create_index(op.f("ix_prompt_templates_is_active"), "prompt_templates", ["is_active"], unique=False)

    # ---- ai_generation_runs ----
    # project_id / task_id / episode_id are PLAIN INTEGER (not FKs) so this
    # telemetry outlives the rows it references — same rule as audit_logs.
    op.create_table(
        "ai_generation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_template_key", sa.String(length=64), nullable=True),
        sa.Column("prompt_template_version", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_generation_runs_project_id"), "ai_generation_runs", ["project_id"], unique=False)
    op.create_index(op.f("ix_ai_generation_runs_task_id"), "ai_generation_runs", ["task_id"], unique=False)
    op.create_index(op.f("ix_ai_generation_runs_episode_id"), "ai_generation_runs", ["episode_id"], unique=False)
    op.create_index(op.f("ix_ai_generation_runs_prompt_template_key"), "ai_generation_runs", ["prompt_template_key"], unique=False)
    op.create_index(op.f("ix_ai_generation_runs_status"), "ai_generation_runs", ["status"], unique=False)
    op.create_index(op.f("ix_ai_generation_runs_created_at"), "ai_generation_runs", ["created_at"], unique=False)

    # ---- seed default prompt templates ----
    _seed_prompts()


def _seed_prompts() -> None:
    from app.services.ai.default_prompts import DEFAULT_PROMPT_TEMPLATES

    prompt_tbl = sa.table(
        "prompt_templates",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("version", sa.Integer),
        sa.column("system_prompt", sa.Text),
        sa.column("user_prompt_template", sa.Text),
        sa.column("output_schema", sa.JSON),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    bind = op.get_bind()
    # Idempotent on (key, version): re-seeding (e.g. after a stamp) never
    # duplicates a row already present, and the uq_prompt_key_version
    # constraint is the hard backstop if two seeders ever race.
    existing = {
        (row[0], row[1])
        for row in bind.execute(
            sa.text("SELECT key, version FROM prompt_templates")
        ).fetchall()
    }

    now = datetime.utcnow()
    rows = []
    for d in DEFAULT_PROMPT_TEMPLATES.values():
        if (d["key"], d["version"]) in existing:
            continue
        rows.append({
            "key": d["key"],
            "name": d["name"],
            "version": d["version"],
            "system_prompt": d["system_prompt"],
            "user_prompt_template": d["user_prompt_template"],
            "output_schema": d.get("output_schema"),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        })
    if rows:
        op.bulk_insert(prompt_tbl, rows)


def downgrade() -> None:
    for ix in ("created_at", "status", "prompt_template_key", "episode_id", "task_id", "project_id"):
        op.drop_index(op.f(f"ix_ai_generation_runs_{ix}"), table_name="ai_generation_runs")
    op.drop_table("ai_generation_runs")

    op.drop_index(op.f("ix_prompt_templates_is_active"), table_name="prompt_templates")
    op.drop_index(op.f("ix_prompt_templates_key"), table_name="prompt_templates")
    op.drop_table("prompt_templates")
