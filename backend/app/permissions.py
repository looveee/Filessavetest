"""Explicit permission matrix.

Replaces the previous rank-based access check (`assert_project_access` with
`min_role`). Every protected endpoint now declares the *permission point* it
requires; this module decides which roles satisfy that point.

Design rules (locked by spec):
  - owner is always the project creator (`projects.owner_id`). It cannot be
    granted via `project_members`. Member rows whose role is `owner` are
    rejected at the API layer (see api/projects.py).
  - manager has every permission EXCEPT `project.delete`. Manager also cannot
    transfer ownership (enforced in the projects API by blocking owner role
    assignment in member ops).
  - reviewer has only review.* permissions on top of read; no task.create.
  - publisher has only schedule.* / account.* on top of read; no script /
    storyboard / task changes.
  - viewer is read-only.
  - System-level `is_admin=True` users implicitly satisfy every permission
    in every project.
"""

from enum import Enum
from typing import Dict, Set, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .models import User, Project, ProjectMember, ProjectRole


class Permission(str, Enum):
    # ---- project ----
    PROJECT_READ = "project.read"
    PROJECT_UPDATE = "project.update"
    PROJECT_DELETE = "project.delete"
    # ---- members ----
    MEMBER_MANAGE = "member.manage"
    # ---- source novel ----
    SOURCE_UPLOAD = "source.upload"
    # ---- episodes ----
    EPISODE_READ = "episode.read"
    # ---- scripts ----
    SCRIPT_CREATE = "script.create"
    SCRIPT_UPDATE = "script.update"
    # ---- storyboards ----
    STORYBOARD_CREATE = "storyboard.create"
    STORYBOARD_UPDATE = "storyboard.update"
    # ---- tasks ----
    TASK_CREATE = "task.create"
    TASK_ASSIGN = "task.assign"
    TASK_RETRY = "task.retry"
    # ---- review ----
    REVIEW_APPROVE = "review.approve"
    REVIEW_REJECT = "review.reject"
    REVIEW_REWRITE = "review.rewrite"   # also covers enhance_*
    # ---- assets ----
    ASSET_READ = "asset.read"
    # ---- schedules ----
    SCHEDULE_CREATE = "schedule.create"
    SCHEDULE_UPDATE = "schedule.update"
    # ---- accounts ----
    ACCOUNT_CREATE = "account.create"
    ACCOUNT_USE = "account.use"


_ALL: Set[Permission] = set(Permission)

# Owner is the only role that may delete the project.
_MANAGER: Set[Permission] = _ALL - {Permission.PROJECT_DELETE}

_EDITOR: Set[Permission] = {
    Permission.PROJECT_READ,
    Permission.SOURCE_UPLOAD,
    Permission.EPISODE_READ,
    Permission.SCRIPT_CREATE,
    Permission.SCRIPT_UPDATE,
    Permission.STORYBOARD_CREATE,
    Permission.STORYBOARD_UPDATE,
    Permission.TASK_CREATE,
    Permission.TASK_RETRY,
    Permission.ASSET_READ,
}

_STORYBOARDER: Set[Permission] = {
    Permission.PROJECT_READ,
    Permission.EPISODE_READ,
    Permission.STORYBOARD_CREATE,
    Permission.STORYBOARD_UPDATE,
    Permission.TASK_CREATE,    # storyboard generation tasks
    Permission.TASK_RETRY,
    Permission.ASSET_READ,
}

_REVIEWER: Set[Permission] = {
    Permission.PROJECT_READ,
    Permission.EPISODE_READ,
    Permission.REVIEW_APPROVE,
    Permission.REVIEW_REJECT,
    Permission.REVIEW_REWRITE,
    Permission.ASSET_READ,
}

_PUBLISHER: Set[Permission] = {
    Permission.PROJECT_READ,
    Permission.ASSET_READ,
    Permission.SCHEDULE_CREATE,
    Permission.SCHEDULE_UPDATE,
    Permission.ACCOUNT_CREATE,
    Permission.ACCOUNT_USE,
}

_VIEWER: Set[Permission] = {
    Permission.PROJECT_READ,
    Permission.EPISODE_READ,
    Permission.ASSET_READ,
}


ROLE_PERMISSIONS: Dict[ProjectRole, Set[Permission]] = {
    ProjectRole.owner:        _ALL,
    ProjectRole.manager:      _MANAGER,
    ProjectRole.editor:       _EDITOR,
    ProjectRole.storyboarder: _STORYBOARDER,
    ProjectRole.reviewer:     _REVIEWER,
    ProjectRole.publisher:    _PUBLISHER,
    ProjectRole.viewer:       _VIEWER,
}


# Permissions that are NOT tied to a particular project.
# `account.create` is a personal-pool action: any active logged-in user may
# create their own account entries. Binding an account to a project's
# schedule still requires `account.use` + `schedule.create` on that project.
USER_LEVEL_PERMISSIONS: Set[Permission] = {Permission.ACCOUNT_CREATE}


# ---------- helpers ----------

def get_user_role(db: Session, user: User, project_id: int) -> Optional[ProjectRole]:
    """Resolve the user's effective role on a project (no admin override)."""
    proj = db.query(Project).filter(Project.id == project_id).first()
    if not proj:
        return None
    if proj.owner_id == user.id:
        return ProjectRole.owner
    m = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user.id,
    ).first()
    return m.role if m else None


def has_permission(
    db: Session,
    user: User,
    project_id: Optional[int],
    perm: Permission,
) -> bool:
    if not user.is_active:
        return False
    if user.is_admin:
        return True   # global admin override

    if perm in USER_LEVEL_PERMISSIONS and project_id is None:
        return True

    if project_id is None:
        return False

    role = get_user_role(db, user, project_id)
    if role is None:
        return False
    return perm in ROLE_PERMISSIONS.get(role, set())


def require_permission(
    db: Session,
    user: User,
    project_id: Optional[int],
    perm: Permission,
) -> None:
    """Raise 403 if user lacks `perm` on `project_id`."""
    if not has_permission(db, user, project_id, perm):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"missing permission: {perm.value}",
        )


def list_permissions_for_role(role: ProjectRole) -> list:
    return sorted(p.value for p in ROLE_PERMISSIONS.get(role, set()))


def role_permission_matrix() -> Dict[str, list]:
    """For introspection / debug endpoints."""
    return {r.value: list_permissions_for_role(r) for r in ProjectRole}
