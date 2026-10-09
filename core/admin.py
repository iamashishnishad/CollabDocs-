from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, Comment, Document, DocumentVersion, Tag, User, Workspace, WorkspaceMember

admin.site.register(User, UserAdmin)
for m in (Workspace, WorkspaceMember, Document, DocumentVersion, Comment, Tag, AuditLog):
    admin.site.register(m)
