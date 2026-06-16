"""Permission matrix invariants.

These tests are pure logic — no DB or Redis needed. They lock in the
seven spec rules from v0.2/v0.3 so a future refactor can't silently
weaken the access model.
"""
import pytest

from app.permissions import (
    Permission, ROLE_PERMISSIONS, list_permissions_for_role,
    role_permission_matrix, USER_LEVEL_PERMISSIONS,
)
from app.models import ProjectRole


# All write-shaped suffixes — used to assert viewer is read-only.
_WRITE_SUFFIXES = (
    ".create", ".update", ".delete", ".upload", ".assign",
    ".retry", ".approve", ".reject", ".rewrite", ".use", ".manage",
)


def test_owner_has_all_permissions():
    assert ROLE_PERMISSIONS[ProjectRole.owner] == set(Permission)


def test_manager_has_all_except_project_delete():
    mgr = ROLE_PERMISSIONS[ProjectRole.manager]
    assert Permission.PROJECT_DELETE not in mgr
    assert mgr == set(Permission) - {Permission.PROJECT_DELETE}


def test_reviewer_can_review_but_cannot_create_tasks_or_scripts():
    rev = ROLE_PERMISSIONS[ProjectRole.reviewer]
    assert Permission.REVIEW_APPROVE in rev
    assert Permission.REVIEW_REJECT in rev
    assert Permission.REVIEW_REWRITE in rev
    assert Permission.TASK_CREATE not in rev
    assert Permission.SCRIPT_CREATE not in rev
    assert Permission.STORYBOARD_CREATE not in rev


def test_publisher_only_does_account_and_schedule():
    pub = ROLE_PERMISSIONS[ProjectRole.publisher]
    assert Permission.SCHEDULE_CREATE in pub
    assert Permission.SCHEDULE_UPDATE in pub
    assert Permission.ACCOUNT_USE in pub
    # Publisher must NOT touch script/storyboard/task creation
    assert Permission.SCRIPT_CREATE not in pub
    assert Permission.SCRIPT_UPDATE not in pub
    assert Permission.STORYBOARD_CREATE not in pub
    assert Permission.STORYBOARD_UPDATE not in pub
    assert Permission.TASK_CREATE not in pub
    assert Permission.REVIEW_APPROVE not in pub


def test_viewer_is_read_only():
    view = ROLE_PERMISSIONS[ProjectRole.viewer]
    write_perms = {p for p in Permission if any(p.value.endswith(s) for s in _WRITE_SUFFIXES)}
    assert not (view & write_perms), \
        f"viewer leaked write permissions: {sorted(p.value for p in (view & write_perms))}"


def test_editor_can_write_scripts_storyboarder_cannot():
    ed = ROLE_PERMISSIONS[ProjectRole.editor]
    sb = ROLE_PERMISSIONS[ProjectRole.storyboarder]
    assert Permission.SCRIPT_CREATE in ed
    assert Permission.SCRIPT_UPDATE in ed
    # storyboarder explicitly does NOT get script perms
    assert Permission.SCRIPT_CREATE not in sb
    assert Permission.SCRIPT_UPDATE not in sb
    # both can do storyboards
    assert Permission.STORYBOARD_CREATE in sb
    assert Permission.STORYBOARD_CREATE in ed


def test_account_create_is_user_level():
    """`account.create` is intentionally a user-level permission so any
    logged-in user can mint accounts in their personal pool, regardless
    of project membership."""
    assert Permission.ACCOUNT_CREATE in USER_LEVEL_PERMISSIONS


def test_role_permission_matrix_introspection_keys():
    matrix = role_permission_matrix()
    assert set(matrix.keys()) == {r.value for r in ProjectRole}
    # Every value must be a list of strings (permission codes)
    for role, perms in matrix.items():
        assert isinstance(perms, list)
        for p in perms:
            assert isinstance(p, str)
            assert "." in p


@pytest.mark.parametrize("role", list(ProjectRole))
def test_every_role_can_at_least_read_project(role):
    """Sanity: every defined role can at least read the project they're on.
    (Otherwise they couldn't see the project they were added to.)"""
    perms = ROLE_PERMISSIONS[role]
    assert Permission.PROJECT_READ in perms, f"{role} cannot project.read"


def test_permission_codes_are_dotted_namespaces():
    """Each Permission value follows the `noun.verb` shape — guards against
    accidentally adding a `Permission.HACK_THE_PLANET` with no namespace."""
    for p in Permission:
        assert "." in p.value, f"{p.value} is not a noun.verb code"
        head, tail = p.value.split(".", 1)
        assert head and tail
