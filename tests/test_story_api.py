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
        with open(self.stories_file, "w", encoding="utf-8") as stories:
            json.dump([], stories)

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
        index.app.config.update(TESTING=True)
        self.client = index.app.test_client()
        self.headers = {"x-admin-token": "test-admin"}

    def tearDown(self):
        self.file_patch.stop()
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
        self.assertEqual(story["status"], "draft")
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
        self.assertEqual(published["status"], "published")
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
        self.assertEqual(response.get_json()["status"], "draft")
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
        self.assertGreaterEqual(updated["updatedAt"], story["updatedAt"])

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

    def test_public_endpoint_excludes_drafts(self):
        draft = self.create_story("Draft")
        published = self.create_story("Published")
        self.client.patch(f"/api/admin/stories/{published['id']}/publish", headers=self.headers)

        stories = self.client.get("/api/stories").get_json()
        self.assertEqual([story["id"] for story in stories], [published["id"]])
        self.assertNotIn(draft["id"], [story["id"] for story in stories])


if __name__ == "__main__":
    unittest.main()
