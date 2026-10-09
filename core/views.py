from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .filters import apply_filters
from .models import AuditLog, Comment, Document, DocumentVersion, Tag, User, Workspace, WorkspaceMember
from .serializers import (AddMemberSerializer, AuditLogSerializer, CommentSerializer, DocumentSerializer,
                          DocumentVersionSerializer, LoginSerializer, TagSerializer, UserSerializer,
                          WorkspaceMemberSerializer, WorkspaceSerializer)
from .services import WRITE_ROLES, get_role, log_action, member_workspace_ids, require_role

Action = AuditLog.Action
Role = WorkspaceMember.Role
ALL_ROLES = tuple(Role.values)


class UserViewSet(viewsets.ModelViewSet):
    """create = register (public). login is a custom action returning an auth token."""
    serializer_class = UserSerializer

    def get_permissions(self):
        if self.action in ("create", "login"):
            return [AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        qs = User.objects.order_by("username")
        p = self.request.query_params
        search = p.get("search")
        if search:
            qs = qs.filter(Q(username__icontains=search) | Q(email__icontains=search)
                           | Q(first_name__icontains=search) | Q(last_name__icontains=search))
        return apply_filters(qs, p, {
            "ids": ("id__in", "uuidlist"),
            "joined_after": ("date_joined__date__gte", "date"),
            "joined_before": ("date_joined__date__lte", "date"),
        })

    def perform_update(self, serializer):
        if serializer.instance != self.request.user:
            raise PermissionDenied("You can only edit your own profile.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance != self.request.user:
            raise PermissionDenied("You can only delete your own account.")
        instance.delete()

    @action(detail=False, methods=["post"])
    def login(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = authenticate(username=s.validated_data["username"], password=s.validated_data["password"])
        if user is None:
            raise AuthenticationFailed("Invalid username or password.")
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user_id": user.pk})


class WorkspaceViewSet(viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer

    def get_queryset(self):
        qs = (Workspace.objects.filter(pk__in=member_workspace_ids(self.request.user))
              .select_related("owner")
              .annotate(member_count=Count("memberships", distinct=True),
                        document_count=Count("documents", distinct=True)))
        p = self.request.query_params
        if p.get("name"):
            qs = qs.filter(name__icontains=p["name"])
        return apply_filters(qs, p, {
            "is_active": ("is_active", "bool"),
            "created_after": ("created_at__date__gte", "date"),
            "created_before": ("created_at__date__lte", "date"),
            "min_members": ("member_count__gte", "int"),
        })

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        invite_ids = data.pop("invite_user_ids", [])
        try:
            # Workspace + owner membership + invites + audit log: all or nothing.
            with transaction.atomic():
                ws = Workspace.objects.create(owner=request.user, **data)
                WorkspaceMember.objects.create(workspace=ws, user=request.user, role=Role.ADMIN)
                for uid in invite_ids:
                    invitee = User.objects.get(pk=uid)
                    WorkspaceMember.objects.create(workspace=ws, user=invitee, role=Role.VIEWER)
                log_action(request.user, Action.CREATED, "Workspace", ws.pk, ws)
        except User.DoesNotExist:
            return Response({"error": "An invited user does not exist; workspace creation was rolled back."},
                            status=status.HTTP_404_NOT_FOUND)
        except IntegrityError:
            return Response({"error": "Duplicate member in invite list; workspace creation was rolled back."},
                            status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(self.get_queryset().get(pk=ws.pk)).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        require_role(self.request.user, serializer.instance.pk, (Role.ADMIN,))
        with transaction.atomic():
            ws = serializer.save()
            log_action(self.request.user, Action.UPDATED, "Workspace", ws.pk, ws)

    def perform_destroy(self, instance):
        require_role(self.request.user, instance.pk, (Role.ADMIN,))
        with transaction.atomic():
            log_action(self.request.user, Action.DELETED, "Workspace", instance.pk, None)
            instance.delete()

    @action(detail=True, methods=["get", "post"])
    def members(self, request, pk=None):
        ws = self.get_object()
        if request.method == "GET":
            qs = ws.memberships.select_related("user").order_by("joined_at")
            qs = apply_filters(qs, request.query_params, {"role": ("role__in", "list")})
            return Response(WorkspaceMemberSerializer(qs, many=True).data)

        require_role(request.user, ws.pk, (Role.ADMIN,))
        s = AddMemberSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            user = User.objects.get(pk=s.validated_data["user"])
        except User.DoesNotExist:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            with transaction.atomic():
                member = WorkspaceMember.objects.create(workspace=ws, user=user, role=s.validated_data["role"])
                log_action(request.user, Action.CREATED, "WorkspaceMember", member.pk, ws)
        except IntegrityError:
            return Response({"error": f"{user.username} is already a member of this workspace."},
                            status=status.HTTP_409_CONFLICT)
        return Response(WorkspaceMemberSerializer(member).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        ws = self.get_object()
        docs = Document.objects.filter(workspace=ws).aggregate(
            total=Count("id"),
            draft=Count("id", filter=Q(status=Document.Status.DRAFT)),
            published=Count("id", filter=Q(status=Document.Status.PUBLISHED)),
            archived=Count("id", filter=Q(status=Document.Status.ARCHIVED)),
        )
        comments = Comment.objects.filter(document__workspace=ws).aggregate(total=Count("id"))
        versions = DocumentVersion.objects.filter(document__workspace=ws).aggregate(total=Count("id"))
        roles = dict(ws.memberships.values_list("role").annotate(n=Count("id")))
        top = (User.objects.filter(documents__workspace=ws)
               .annotate(document_count=Count("documents", filter=Q(documents__workspace=ws)))
               .order_by("-document_count")[:5])
        return Response({
            "workspace": ws.pk,
            "members": {"total": sum(roles.values()), "by_role": roles},
            "documents": docs,
            "comments": comments["total"],
            "versions": versions["total"],
            "top_contributors": [{"user": u.pk, "username": u.username, "documents": u.document_count} for u in top],
        })


class DocumentViewSet(viewsets.ModelViewSet):
    serializer_class = DocumentSerializer

    def get_queryset(self):
        qs = (Document.objects.filter(workspace_id__in=member_workspace_ids(self.request.user))
              .select_related("workspace", "created_by")
              .prefetch_related("tags")
              .annotate(version_count=Count("versions", distinct=True)))
        p = self.request.query_params
        q = p.get("q")
        if q:  # OR across title, content and tag name
            qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q) | Q(tags__name__icontains=q)).distinct()
        return apply_filters(qs, p, {
            "workspace": ("workspace_id", "uuid"),
            "status": ("status__in", "list"),
            "tag": ("tags__name__in", "list"),
            "created_by": ("created_by_id", "uuid"),
            "title": ("title__icontains", "str"),
            "created_after": ("created_at__date__gte", "date"),
            "created_before": ("created_at__date__lte", "date"),
            "updated_after": ("updated_at__date__gte", "date"),
            "updated_before": ("updated_at__date__lte", "date"),
            "min_versions": ("version_count__gte", "int"),
        })

    def _snapshot(self, doc, editor):
        # Must be called inside transaction.atomic().
        return DocumentVersion.objects.create(
            document=doc, version_number=doc.versions.count() + 1,
            title=doc.title, content=doc.content, edited_by=editor)

    def perform_create(self, serializer):
        # Document save (+ audit log via signal) + first version in one transaction.
        with transaction.atomic():
            doc = serializer.save(created_by=self.request.user)
            self._snapshot(doc, self.request.user)

    def update(self, request, *args, **kwargs):
        require_role(request.user, self.get_object().workspace_id)  # 403 before validation
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        with transaction.atomic():
            doc = serializer.save()  # post_save signal writes the 'updated' AuditLog here
            doc.version_count = self._snapshot(doc, self.request.user).version_number

    def perform_destroy(self, instance):
        require_role(self.request.user, instance.workspace_id, (Role.ADMIN,))
        with transaction.atomic():
            log_action(self.request.user, Action.DELETED, "Document", instance.pk, instance.workspace_id)
            instance.delete()

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        doc = self.get_object()
        qs = DocumentVersion.objects.filter(document=doc).select_related("edited_by")
        qs = apply_filters(qs, request.query_params, {
            "from_version": ("version_number__gte", "int"),
            "to_version": ("version_number__lte", "int"),
            "edited_by": ("edited_by_id", "uuid"),
        })
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(DocumentVersionSerializer(page, many=True).data)

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        doc = self.get_object()
        c = Comment.objects.filter(document=doc).aggregate(
            total=Count("id"),
            top_level=Count("id", filter=Q(parent__isnull=True)),
            replies=Count("id", filter=Q(parent__isnull=False)),
            unique_commenters=Count("author", distinct=True),
        )
        v = DocumentVersion.objects.filter(document=doc).aggregate(
            total=Count("id"), latest=Max("version_number"), editors=Count("edited_by", distinct=True))
        return Response({
            "document": doc.pk, "title": doc.title, "status": doc.status,
            "comments": c, "versions": v,
            "tags": list(doc.tags.values_list("name", flat=True)),
        })

    @action(detail=True, methods=["get", "post", "delete"])
    def tags(self, request, pk=None):
        doc = self.get_object()
        if request.method == "GET":
            return Response(list(doc.tags.values_list("name", flat=True)))
        require_role(request.user, doc.workspace_id)
        names = request.data.get("tags")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
            raise ValidationError({"tags": "Provide a non-empty list of tag names."})
        names = sorted({n.strip().lower() for n in names})
        if request.method == "POST":
            with transaction.atomic():
                tags = [Tag.objects.get_or_create(name=n)[0] for n in names]
                doc.tags.add(*tags)
        else:
            doc.tags.remove(*Tag.objects.filter(name__in=names))
        return Response(list(doc.tags.values_list("name", flat=True)))


class CommentViewSet(viewsets.ModelViewSet):
    serializer_class = CommentSerializer

    def get_queryset(self):
        qs = (Comment.objects.filter(document__workspace_id__in=member_workspace_ids(self.request.user))
              .select_related("author", "document", "parent")
              .annotate(reply_count=Count("replies", distinct=True)))
        p = self.request.query_params
        if p.get("top_level", "").lower() == "true":
            qs = qs.filter(parent__isnull=True)
        return apply_filters(qs, p, {
            "document": ("document_id", "uuid"),
            "parent": ("parent_id", "uuid"),
            "author": ("author_id", "uuid"),
            "search": ("content__icontains", "str"),
            "created_after": ("created_at__date__gte", "date"),
            "created_before": ("created_at__date__lte", "date"),
        })

    def perform_create(self, serializer):
        doc = serializer.validated_data["document"]
        require_role(self.request.user, doc.workspace_id, ALL_ROLES)
        with transaction.atomic():
            c = serializer.save(author=self.request.user)
            log_action(self.request.user, Action.CREATED, "Comment", c.pk, doc.workspace_id)

    def perform_update(self, serializer):
        if serializer.instance.author != self.request.user:
            raise PermissionDenied("Only the author can edit a comment.")
        with transaction.atomic():
            c = serializer.save()
            log_action(self.request.user, Action.UPDATED, "Comment", c.pk, c.document.workspace_id)

    def perform_destroy(self, instance):
        if instance.author != self.request.user:
            require_role(self.request.user, instance.document.workspace_id, (Role.ADMIN,))
        with transaction.atomic():
            log_action(self.request.user, Action.DELETED, "Comment", instance.pk, instance.document.workspace_id)
            instance.delete()


class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = Tag.objects.annotate(document_count=Count("documents", distinct=True)).order_by("name")
        return apply_filters(qs, self.request.query_params, {
            "name": ("name__icontains", "str"),
            "min_documents": ("document_count__gte", "int"),
        })


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        qs = AuditLog.objects.filter(workspace_id__in=member_workspace_ids(self.request.user)).select_related("actor")
        return apply_filters(qs, self.request.query_params, {
            "workspace": ("workspace_id", "uuid"),
            "model_name": ("model_name", "str"),
            "action": ("action__in", "list"),
            "object_id": ("object_id", "uuid"),
            "actor": ("actor_id", "uuid"),
            "from": ("created_at__date__gte", "date"),
            "to": ("created_at__date__lte", "date"),
        })
