from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import AuditLog, Document


@receiver(pre_save, sender=Document)
def remember_if_adding(sender, instance, **kwargs):
    # Django flips _state.adding to False before post_save fires, so capture it here.
    instance._was_adding = instance._state.adding


@receiver(post_save, sender=Document)
def audit_document_save(sender, instance, **kwargs):
    # Runs inside the caller's transaction.atomic(), so the log rolls back with the save.
    AuditLog.objects.create(
        actor=instance.created_by,
        workspace_id=instance.workspace_id,
        action=AuditLog.Action.CREATED if instance._was_adding else AuditLog.Action.UPDATED,
        model_name="Document",
        object_id=instance.pk,
    )
