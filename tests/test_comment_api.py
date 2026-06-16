import json
import os
import tempfile
import unittest
from unittest.mock import patch

from api import index


class CommentApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.comments_file = os.path.join(self.temp_dir.name, "comments.json")
        with open(self.comments_file, "w", encoding="utf-8") as comments:
            json.dump([], comments)

        self.environment = patch.dict(os.environ, {"ADMIN_TOKEN": "test-admin"}, clear=False)
        self.environment.start()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_KEY", None)
        self.file_patch = patch.object(index, "COMMENTS_FILE", self.comments_file)
        self.file_patch.start()
        index.app.config.update(TESTING=True)
        self.client = index.app.test_client()
        self.admin_headers = {"x-admin-token": "test-admin"}

    def tearDown(self):
        self.file_patch.stop()
        self.environment.stop()
        self.temp_dir.cleanup()

    def create_comment(self, story_id="story-123"):
        response = self.client.post(
            "/api/comments",
            json={
                "story_id": story_id,
                "author_name": "John Reader",
                "author_email": "john@example.com",
                "content": "Great article",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["comment"]

    def set_status(self, comment_id, action):
        return self.client.patch(
            f"/api/admin/comments/{comment_id}/{action}",
            headers=self.admin_headers,
        )

    def public_comments(self, story_id="story-123"):
        response = self.client.get(f"/api/stories/{story_id}/comments")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_create_comment_defaults_to_pending_review(self):
        response = self.client.post(
            "/api/comments",
            json={
                "story_id": "story-123",
                "author_name": "John Reader",
                "author_email": "john@example.com",
                "content": "Great article",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(response.get_json()["status"], "PENDING_REVIEW")
        self.assertEqual(response.get_json()["comment"]["status"], "PENDING_REVIEW")

    def test_create_comment_validation(self):
        invalid_payloads = [
            {"author_name": "John", "author_email": "john@example.com", "content": "Text"},
            {"story_id": "1", "author_name": "", "author_email": "john@example.com", "content": "Text"},
            {"story_id": "1", "author_name": "John", "author_email": "bad", "content": "Text"},
            {"story_id": "1", "author_name": "John", "author_email": "john@example.com", "content": ""},
            {
                "story_id": "1",
                "author_name": "John",
                "author_email": "john@example.com",
                "content": "Text",
                "status": "APPROVED_BY_ME",
            },
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/comments", json=payload).status_code, 400)

    def test_pending_comments_are_hidden_publicly(self):
        self.create_comment()
        self.assertEqual(self.public_comments(), [])

    def test_approve_endpoint_makes_comment_public(self):
        comment = self.create_comment()
        response = self.set_status(comment["id"], "approve")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "APPROVED")
        self.assertEqual([item["id"] for item in self.public_comments()], [comment["id"]])

    def test_reject_endpoint_hides_comment(self):
        comment = self.create_comment()
        self.set_status(comment["id"], "approve")
        response = self.set_status(comment["id"], "reject")
        self.assertEqual(response.get_json()["status"], "REJECTED")
        self.assertEqual(self.public_comments(), [])

    def test_spam_endpoint_hides_comment(self):
        comment = self.create_comment()
        self.set_status(comment["id"], "approve")
        response = self.set_status(comment["id"], "spam")
        self.assertEqual(response.get_json()["status"], "SPAM")
        self.assertEqual(self.public_comments(), [])

    def test_pending_endpoint_returns_comment_to_review(self):
        comment = self.create_comment()
        self.set_status(comment["id"], "approve")
        response = self.set_status(comment["id"], "pending")
        self.assertEqual(response.get_json()["status"], "PENDING_REVIEW")
        self.assertEqual(self.public_comments(), [])

    def test_delete_endpoint_permanently_deletes_comment(self):
        comment = self.create_comment()
        response = self.client.delete(
            f"/api/admin/comments/{comment['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        self.assertEqual(
            self.client.delete(
                f"/api/admin/comments/{comment['id']}",
                headers=self.admin_headers,
            ).status_code,
            404,
        )

    def test_admin_status_filtering(self):
        pending = self.create_comment("story-1")
        approved = self.create_comment("story-2")
        self.set_status(approved["id"], "approve")
        response = self.client.get(
            "/api/admin/comments?status=APPROVED",
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.get_json()], [approved["id"]])
        self.assertNotIn(pending["id"], [item["id"] for item in response.get_json()])
        self.assertEqual(
            self.client.get(
                "/api/admin/comments?status=INVALID",
                headers=self.admin_headers,
            ).status_code,
            400,
        )

    def test_admin_comments_are_sorted_newest_first(self):
        first = self.create_comment("story-1")
        second = self.create_comment("story-2")
        response = self.client.get("/api/admin/comments", headers=self.admin_headers)
        self.assertEqual([item["id"] for item in response.get_json()], [second["id"], first["id"]])

    def test_admin_endpoints_require_authorization(self):
        comment = self.create_comment()
        protected = [
            ("get", "/api/admin/comments"),
            ("patch", f"/api/admin/comments/{comment['id']}/approve"),
            ("patch", f"/api/admin/comments/{comment['id']}/reject"),
            ("patch", f"/api/admin/comments/{comment['id']}/spam"),
            ("patch", f"/api/admin/comments/{comment['id']}/pending"),
            ("delete", f"/api/admin/comments/{comment['id']}"),
        ]
        for method, path in protected:
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 403)

    def test_legacy_public_endpoint_only_returns_approved_comments(self):
        comment = self.create_comment("legacy-slug")
        self.assertEqual(self.client.get("/api/comments?path=story:legacy-slug").get_json(), [])
        self.set_status(comment["id"], "approve")
        response = self.client.get("/api/comments?path=story:legacy-slug")
        self.assertEqual([item["id"] for item in response.get_json()], [comment["id"]])

    def test_legacy_moderation_endpoint_preserves_rejected_comment(self):
        comment = self.create_comment()
        response = self.client.post(
            "/api/admin/comments/moderate",
            headers=self.admin_headers,
            json={"id": comment["id"], "action": "reject"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["comment"]["status"], "REJECTED")
        admin_comments = self.client.get("/api/admin/comments", headers=self.admin_headers).get_json()
        self.assertEqual(admin_comments[0]["id"], comment["id"])


if __name__ == "__main__":
    unittest.main()
