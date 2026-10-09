from rest_framework.test import APITestCase

from .models import AuditLog, Document, DocumentVersion, User, Workspace, WorkspaceMember


class FlowTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", "a@x.com", "pass1234x")
        self.bob = User.objects.create_user("bob", "b@x.com", "pass1234x")
        self.client.force_authenticate(self.alice)

    def make_ws(self, **extra):
        return self.client.post("/api/workspaces/", {"name": "Team", **extra}, format="json")

    def test_workspace_create_adds_admin(self):
        r = self.make_ws()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["my_role"], "admin")
        self.assertEqual(r.data["member_count"], 1)

    def test_workspace_rollback_on_bad_invite(self):
        r = self.make_ws(invite_user_ids=[str(self.bob.pk), "00000000-0000-4000-8000-000000000000"])
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Workspace.objects.count(), 0)
        self.assertEqual(WorkspaceMember.objects.count(), 0)

    def test_workspace_rollback_on_duplicate(self):
        r = self.make_ws(invite_user_ids=[str(self.bob.pk), str(self.bob.pk)])
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Workspace.objects.count(), 0)

    def test_duplicate_member_409(self):
        ws = self.make_ws().data["id"]
        url = f"/api/workspaces/{ws}/members/"
        self.assertEqual(self.client.post(url, {"user": str(self.bob.pk), "role": "editor"}, format="json").status_code, 201)
        self.assertEqual(self.client.post(url, {"user": str(self.bob.pk)}, format="json").status_code, 409)

    def test_document_versions_and_audit(self):
        ws = self.make_ws().data["id"]
        d = self.client.post("/api/documents/", {"workspace": ws, "title": "Spec", "content": "v1"}, format="json")
        self.assertEqual(d.status_code, 201)
        did = d.data["id"]
        u = self.client.put(f"/api/documents/{did}/", {"workspace": ws, "title": "Spec", "content": "v2"}, format="json")
        self.assertEqual(u.status_code, 200)
        self.assertEqual(u.data["version_count"], 2)
        self.assertEqual(list(DocumentVersion.objects.filter(document_id=did).values_list("version_number", flat=True).order_by("version_number")), [1, 2])
        self.assertEqual(list(AuditLog.objects.filter(object_id=did).order_by("created_at").values_list("action", flat=True)), ["created", "updated"])
        self.assertEqual(self.client.get(f"/api/documents/{did}/summary/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/workspaces/{ws}/stats/").data["documents"]["total"], 1)

    def test_viewer_cannot_write_and_nonmember_404(self):
        ws = self.make_ws().data["id"]
        self.client.post(f"/api/workspaces/{ws}/members/", {"user": str(self.bob.pk), "role": "viewer"}, format="json")
        did = self.client.post("/api/documents/", {"workspace": ws, "title": "T"}, format="json").data["id"]
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.post("/api/documents/", {"workspace": ws, "title": "X"}, format="json").status_code, 400)
        self.assertEqual(self.client.put(f"/api/documents/{did}/", {"title": "Y"}, format="json").status_code, 403)
        carol = User.objects.create_user("carol", "c@x.com", "pass1234x")
        self.client.force_authenticate(carol)
        self.assertEqual(self.client.get(f"/api/documents/{did}/").status_code, 404)

    def test_comments_tags_filters(self):
        ws = self.make_ws().data["id"]
        did = self.client.post("/api/documents/", {"workspace": ws, "title": "Python guide", "content": "x"}, format="json").data["id"]
        c = self.client.post("/api/comments/", {"document": did, "content": "hi"}, format="json")
        self.assertEqual(c.status_code, 201)
        r = self.client.post("/api/comments/", {"document": did, "content": "re", "parent": c.data["id"]}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.client.get("/api/comments/?top_level=true").data["results"][0]["reply_count"], 1)
        self.assertEqual(self.client.post(f"/api/documents/{did}/tags/", {"tags": ["Python", "api"]}, format="json").status_code, 200)
        self.assertEqual(self.client.get("/api/documents/?q=python&status=draft,published&tag=api").data["count"], 1)
        self.assertEqual(self.client.get("/api/documents/?created_after=bad").status_code, 400)
        self.assertEqual(self.client.get("/api/tags/?min_documents=1").data["count"], 2)
        self.assertGreaterEqual(self.client.get("/api/audit-logs/?model_name=Document").data["count"], 1)

    def test_register_login(self):
        self.client.force_authenticate(None)
        r = self.client.post("/api/users/", {"username": "dan", "email": "d@x.com", "password": "pass1234x"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.client.post("/api/users/login/", {"username": "dan", "password": "pass1234x"}, format="json").status_code, 200)
        self.assertEqual(self.client.post("/api/users/login/", {"username": "dan", "password": "no"}, format="json").status_code, 401)
