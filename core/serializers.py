from rest_framework import serializers

from .models import AuditLog, Comment, Document, DocumentVersion, Tag, User, Workspace, WorkspaceMember
from .services import WRITE_ROLES, get_role


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "full_name", "password", "date_joined"]
        read_only_fields = ["id", "date_joined"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def validate_password(self, value):
        if value.isdigit() or value.isalpha():
            raise serializers.ValidationError("Password must mix letters and numbers.")
        return value

    def validate_email(self, value):
        value = value.lower()
        qs = User.objects.filter(email__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class WorkspaceSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="owner.username", read_only=True)
    member_count = serializers.SerializerMethodField()
    document_count = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()
    invite_user_ids = serializers.ListField(child=serializers.UUIDField(), write_only=True, required=False)

    class Meta:
        model = Workspace
        fields = ["id", "name", "description", "owner", "owner_username", "is_active", "member_count",
                  "document_count", "my_role", "created_at", "invite_user_ids"]
        read_only_fields = ["id", "owner", "created_at"]

    def get_member_count(self, obj):
        return obj.member_count if hasattr(obj, "member_count") else obj.memberships.count()

    def get_document_count(self, obj):
        return obj.document_count if hasattr(obj, "document_count") else obj.documents.count()

    def get_my_role(self, obj):
        return get_role(self.context["request"].user, obj.pk)

    def validate_name(self, value):
        value = value.strip()
        if len(value) < 3:
            raise serializers.ValidationError("Workspace name must be at least 3 characters.")
        return value


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = WorkspaceMember
        fields = ["id", "user", "username", "role", "joined_at"]


class AddMemberSerializer(serializers.Serializer):
    user = serializers.UUIDField()
    role = serializers.ChoiceField(choices=WorkspaceMember.Role.choices, default=WorkspaceMember.Role.VIEWER)


class TagSerializer(serializers.ModelSerializer):
    document_count = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = ["id", "name", "document_count"]

    def get_document_count(self, obj):
        return obj.document_count if hasattr(obj, "document_count") else obj.documents.count()

    def validate_name(self, value):
        value = value.strip().lower()
        if not value:
            raise serializers.ValidationError("Tag name cannot be blank.")
        if Tag.objects.filter(name=value).exists():
            raise serializers.ValidationError("Tag already exists.")
        return value


class DocumentSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    workspace_name = serializers.CharField(source="workspace.name", read_only=True)
    tags = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")
    version_count = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ["id", "workspace", "workspace_name", "title", "content", "status", "created_by",
                  "created_by_username", "tags", "version_count", "created_at", "updated_at"]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def get_version_count(self, obj):
        return obj.version_count if hasattr(obj, "version_count") else obj.versions.count()

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Title cannot be blank.")
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        if self.instance:
            ws = attrs.get("workspace")
            if ws and ws.pk != self.instance.workspace_id:
                raise serializers.ValidationError({"workspace": "A document cannot be moved to another workspace."})
        else:
            ws = attrs["workspace"]
            if not ws.is_active:
                raise serializers.ValidationError({"workspace": "Workspace is inactive."})
            if get_role(user, ws.pk) not in WRITE_ROLES:
                raise serializers.ValidationError({"workspace": "You need admin or editor role in this workspace."})
        return attrs


class DocumentVersionSerializer(serializers.ModelSerializer):
    edited_by_username = serializers.CharField(source="edited_by.username", read_only=True)

    class Meta:
        model = DocumentVersion
        fields = ["id", "document", "version_number", "title", "content", "edited_by",
                  "edited_by_username", "created_at"]


class CommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source="author.username", read_only=True)
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ["id", "document", "author", "author_username", "content", "parent", "reply_count", "created_at"]
        read_only_fields = ["id", "author", "created_at"]

    def get_reply_count(self, obj):
        return obj.reply_count if hasattr(obj, "reply_count") else obj.replies.count()

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("Comment cannot be blank.")
        return value.strip()

    def validate(self, attrs):
        if self.instance:
            if "document" in attrs and attrs["document"].pk != self.instance.document_id:
                raise serializers.ValidationError({"document": "Cannot move a comment to another document."})
            if "parent" in attrs and attrs["parent"] != self.instance.parent:
                raise serializers.ValidationError({"parent": "Cannot re-parent a comment."})
            return attrs
        parent = attrs.get("parent")
        if parent and parent.document_id != attrs["document"].pk:
            raise serializers.ValidationError({"parent": "Parent comment belongs to a different document."})
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "actor_username", "workspace", "action", "model_name", "object_id", "created_at"]
