from rest_framework.exceptions import PermissionDenied

from .models import AuditLog, WorkspaceMember

WRITE_ROLES = (WorkspaceMember.Role.ADMIN, WorkspaceMember.Role.EDITOR)


def get_role(user, workspace_id):
    return (
        WorkspaceMember.objects.filter(user=user, workspace_id=workspace_id)
        .values_list("role", flat=True)
        .first()
    )


def require_role(user, workspace_id, allowed=WRITE_ROLES):
    role = get_role(user, workspace_id)
    if role not in allowed:
        raise PermissionDenied("Your role in this workspace does not allow this action.")
    return role


def member_workspace_ids(user):
    """Subquery of workspace IDs the user belongs to (values_list: IDs only)."""
    return WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)


def log_action(actor, action, model_name, object_id, workspace=None):
    """Call inside the same transaction.atomic() block as the action it records."""
    return AuditLog.objects.create(
        actor=actor, action=action, model_name=model_name, object_id=object_id,
        workspace_id=getattr(workspace, "pk", workspace),
    )
