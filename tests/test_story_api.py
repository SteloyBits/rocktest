import json
import os
import tempfile
import unittest
from unittest.mock import patch

from api import index


class StoryApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stories_file = os.path.join(self.temp_dir.name, "stories.json")
        self.comments_file = os.path.join(self.temp_dir.name, "comments.json")
        with open(self.stories_file, "w", encoding="utf-8") as stories:
            json.dump([], stories)
        with open(self.comments_file, "w", encoding="utf-8") as comments:
            json.dump([], comments)

        self.environment = patch.dict(
            os.environ,
            {"ADMIN_TOKEN": "test-admin"},
            clear=False,
        )
        self.environment.start()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_KEY", None)
        self.file_patch = patch.object(index, "STORIES_FILE", self.stories_file)
        self.file_patch.start()
        self.comments_file_patch = patch.object(index, "COMMENTS_FILE", self.comments_file)
        self.comments_file_patch.start()
        index.app.config.update(TESTING=True)
        self.client = index.app.test_client()
        self.headers = {"x-admin-token": "test-admin"}

    def tearDown(self):
        self.file_patch.stop()
        self.comments_file_patch.stop()
        self.environment.stop()
        self.temp_dir.cleanup()

    def create_story(self, title="Breaking News"):
        response = self.client.post(
            "/api/admin/stories",
            headers=self.headers,
            json={
                "title": title,
                "content": "Complete story content.",
                "coverImage": "https://example.com/cover.jpg",
                "tags": ["sports", "sports"],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def test_create_story_defaults_to_draft_and_validates_input(self):
        story = self.create_story()
        self.assertEqual(story["status"], "DRAFT")
        self.assertEqual(story["slug"], "breaking-news")
        self.assertEqual(story["tags"], ["sports"])
        self.assertIsNone(story["publishedAt"])

        invalid = self.client.post(
            "/api/admin/stories",
            headers=self.headers,
            json={"title": " ", "content": "Body", "coverImage": "image"},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_fetch_admin_stories_returns_drafts_and_published(self):
        first = self.create_story("First")
        second = self.create_story("Second")
        self.client.patch(f"/api/admin/stories/{first['id']}/publish", headers=self.headers)

        response = self.client.get("/api/admin/stories", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        stories = response.get_json()
        self.assertEqual({story["id"] for story in stories}, {first["id"], second["id"]})
        self.assertEqual(stories[0]["id"], first["id"])

    def test_publish_story_makes_it_public(self):
        story = self.create_story()
        response = self.client.patch(
            f"/api/admin/stories/{story['id']}/publish",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        published = response.get_json()
        self.assertEqual(published["status"], "PUBLISHED")
        self.assertIsNotNone(published["publishedAt"])

        public = self.client.get("/api/stories").get_json()
        self.assertEqual([item["id"] for item in public], [story["id"]])
        self.assertEqual(public[0]["headline"], story["title"])

    def test_mark_story_as_draft_removes_it_from_public_endpoints(self):
        story = self.create_story()
        self.client.patch(f"/api/admin/stories/{story['id']}/publish", headers=self.headers)
        response = self.client.patch(
            f"/api/admin/stories/{story['id']}/draft",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "DRAFT")
        self.assertIsNone(response.get_json()["publishedAt"])
        self.assertEqual(self.client.get("/api/stories").get_json(), [])
        self.assertEqual(self.client.get(f"/api/stories/{story['slug']}").status_code, 404)

    def test_edit_story_updates_fields_and_regenerates_slug(self):
        story = self.create_story()
        response = self.client.put(
            f"/api/admin/stories/{story['id']}",
            headers=self.headers,
            json={
                "title": "Updated Headline",
                "content": "Updated content.",
                "coverImage": "https://example.com/new.jpg",
                "tags": ["news"],
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = response.get_json()
        self.assertEqual(updated["slug"], "updated-headline")
        self.assertEqual(updated["content"], "Updated content.")
        self.assertEqual(updated["coverImage"], "https://example.com/new.jpg")
        self.assertEqual(updated["status"], "DRAFT")
        self.assertGreaterEqual(updated["updatedAt"], story["updatedAt"])

        stories = self.client.get("/api/admin/stories", headers=self.headers).get_json()
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["id"], story["id"])

        invalid_status = self.client.put(
            f"/api/admin/stories/{story['id']}",
            headers=self.headers,
            json={"status": "invalid"},
        )
        self.assertEqual(invalid_status.status_code, 400)

    def test_delete_story_permanently_deletes_it(self):
        story = self.create_story()
        response = self.client.delete(
            f"/api/admin/stories/{story['id']}",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        self.assertEqual(self.client.get("/api/admin/stories", headers=self.headers).get_json(), [])
        self.assertEqual(
            self.client.delete(f"/api/admin/stories/{story['id']}", headers=self.headers).status_code,
            404,
        )

    def test_delete_published_story_removes_it_from_public_list(self):
        story = self.create_story()
        self.client.patch(f"/api/admin/stories/{story['id']}/publish", headers=self.headers)

        response = self.client.delete(
            f"/api/admin/stories/{story['id']}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        self.assertEqual(self.client.get("/api/stories").get_json(), [])

    def test_public_endpoint_excludes_drafts(self):
        draft = self.create_story("Draft")
        published = self.create_story("Published")
        self.client.patch(f"/api/admin/stories/{published['id']}/publish", headers=self.headers)

        stories = self.client.get("/api/stories").get_json()
        self.assertEqual([story["id"] for story in stories], [published["id"]])
        self.assertNotIn(draft["id"], [story["id"] for story in stories])

    def test_update_accepts_legacy_payload_and_metadata_without_creating_duplicate(self):
        story = self.create_story()
        self.client.patch(f"/api/admin/stories/{story['id']}/publish", headers=self.headers)

        response = self.client.put(
            f"/api/admin/stories/{story['id']}",
            headers=self.headers,
            json={
                "headline": "Legacy Payload Title",
                "body": "Legacy body content.",
                "image_url": "https://example.com/legacy.jpg",
                "tags": ["culture,news"],
                "excerpt": "Legacy excerpt",
                "meta_description": "Legacy meta",
                "category": "culture",
                "quality_score": "91.5",
            },
        )

        self.assertEqual(response.status_code, 200)
        updated = response.get_json()
        self.assertEqual(updated["id"], story["id"])
        self.assertEqual(updated["title"], "Legacy Payload Title")
        self.assertEqual(updated["content"], "Legacy body content.")
        self.assertEqual(updated["coverImage"], "https://example.com/legacy.jpg")
        self.assertEqual(updated["tags"], ["culture", "news"])
        self.assertEqual(updated["status"], "PUBLISHED")
        self.assertEqual(updated["metaDescription"], "Legacy meta")
        self.assertEqual(updated["category"], "culture")
        self.assertEqual(updated["qualityScore"], 91.5)

        stories = self.client.get("/api/admin/stories", headers=self.headers).get_json()
        self.assertEqual(len(stories), 1)

    def test_image_url_update_is_reflected_publicly_after_publish(self):
        story = self.create_story()
        self.client.patch(f"/api/admin/stories/{story['id']}/publish", headers=self.headers)

        response = self.client.put(
            f"/api/admin/stories/{story['id']}",
            headers=self.headers,
            json={"coverImage": "https://cdn.example.com/final.jpg"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["coverImage"], "https://cdn.example.com/final.jpg")
        public = self.client.get("/api/stories").get_json()
        self.assertEqual(public[0]["image_url"], "https://cdn.example.com/final.jpg")
        self.assertEqual(public[0]["coverImage"], "https://cdn.example.com/final.jpg")

    def test_public_stories_order_by_newest_published_then_created_at(self):
        older = self.create_story("Older")
        newer = self.create_story("Newer")
        self.client.patch(f"/api/admin/stories/{older['id']}/publish", headers=self.headers)
        self.client.patch(f"/api/admin/stories/{newer['id']}/publish", headers=self.headers)

        stories = self.client.get("/api/stories").get_json()

        self.assertEqual([story["id"] for story in stories], [newer["id"], older["id"]])

    def test_legacy_validated_stories_are_public_and_ordered_by_created_at_fallback(self):
        with open(self.stories_file, "w", encoding="utf-8") as stories:
            json.dump(
                [
                    {
                        "id": "old",
                        "headline": "Old",
                        "body": "Old content",
                        "image_url": "https://example.com/old.jpg",
                        "slug": "old",
                        "status": "validated",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "tags": [],
                    },
                    {
                        "id": "new",
                        "headline": "New",
                        "body": "New content",
                        "image_url": "https://example.com/new.jpg",
                        "slug": "new",
                        "status": "validated",
                        "created_at": "2026-02-01T00:00:00+00:00",
                        "tags": [],
                    },
                ],
                stories,
            )

        response = self.client.get("/api/stories")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([story["id"] for story in response.get_json()], ["new", "old"])

    def test_legacy_update_route_uses_existing_record(self):
        story = self.create_story()

        response = self.client.put(
            f"/api/stories/{story['id']}",
            headers=self.headers,
            json={
                "headline": "Legacy Route Title",
                "body": "Updated through legacy route.",
                "image_url": "https://example.com/route.jpg",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["id"], story["id"])
        self.assertEqual(response.get_json()["title"], "Legacy Route Title")
        self.assertEqual(len(self.client.get("/api/admin/stories", headers=self.headers).get_json()), 1)

    def test_legacy_delete_route_deletes_published_story(self):
        story = self.create_story()
        self.client.patch(f"/api/admin/stories/{story['id']}/publish", headers=self.headers)

        response = self.client.delete(f"/api/stories/{story['id']}", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        self.assertEqual(self.client.get("/api/stories").get_json(), [])

    def test_delete_story_removes_associated_comments(self):
        story = self.create_story()
        comment_response = self.client.post(
            "/api/comments",
            json={
                "story_id": story["id"],
                "author_name": "Reader",
                "author_email": "reader@example.com",
                "content": "Please remove me with the story.",
            },
        )
        comment_id = comment_response.get_json()["comment"]["id"]

        response = self.client.delete(
            f"/api/admin/stories/{story['id']}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get("/api/admin/comments", headers=self.headers).get_json(),
            [],
        )
        self.assertEqual(
            self.client.delete(f"/api/admin/comments/{comment_id}", headers=self.headers).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
