import re
from datetime import datetime, timezone
from urllib.parse import urlparse


DRAFT = "DRAFT"
PUBLISHED = "PUBLISHED"
VALID_STATUSES = {DRAFT, PUBLISHED}
PUBLISHED_LEGACY_STATUSES = {"published", "PUBLISHED", "validated", "VALIDATED"}
DRAFT_LEGACY_STATUSES = {"draft", "DRAFT", "review", "REVIEW"}


class StoryValidationError(ValueError):
    pass


class StoryNotFoundError(LookupError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "story"


def normalize_status(value, default=DRAFT):
    if value is None or value == "":
        return default
    if value in PUBLISHED_LEGACY_STATUSES:
        return PUBLISHED
    if value in DRAFT_LEGACY_STATUSES:
        return DRAFT
    raise StoryValidationError("status must be DRAFT or PUBLISHED")


def is_public_status(value):
    try:
        return normalize_status(value) == PUBLISHED
    except StoryValidationError:
        return False


def validate_image_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StoryValidationError("coverImage must be a valid http or https URL")


def normalize_tags(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise StoryValidationError("tags must be an array of strings")

    result = []
    seen = set()
    for tag in value:
        if not isinstance(tag, str):
            raise StoryValidationError("tags must be an array of strings")
        for part in tag.split(","):
            normalized = part.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
    return result


def canonical_story(record):
    title = record.get("title", record.get("headline", ""))
    content = record.get("content", record.get("body", ""))
    cover_image = record.get("coverImage", record.get("cover_image", record.get("image_url", "")))
    created_at = record.get("createdAt", record.get("created_at"))
    updated_at = record.get("updatedAt", record.get("updated_at", created_at))
    published_at = record.get("publishedAt", record.get("published_at"))

    return {
        "id": str(record.get("id", "")),
        "title": title,
        "slug": record.get("slug", ""),
        "content": content,
        "coverImage": cover_image,
        "tags": record.get("tags") or [],
        "status": normalize_status(record.get("status"), DRAFT),
        "publishedAt": published_at,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "excerpt": record.get("excerpt", ""),
        "metaDescription": record.get("metaDescription", record.get("meta_description", "")),
        "category": record.get("category", ""),
        "qualityScore": record.get("qualityScore", record.get("quality_score")),
    }


def public_story(record):
    story = canonical_story(record)
    # Keep the current public frontend contract while exposing the canonical model.
    story.update(
        {
            "headline": story["title"],
            "body": story["content"],
            "image_url": story["coverImage"],
            "created_at": story["createdAt"],
            "updated_at": story["updatedAt"],
            "published_at": story["publishedAt"],
            "excerpt": record.get("excerpt", ""),
            "meta_description": story["metaDescription"],
            "category": story["category"],
            "quality_score": story["qualityScore"],
        }
    )
    return story


def storage_story(story, existing=None):
    existing = dict(existing or {})
    existing.update(
        {
            "id": story["id"],
            "title": story["title"],
            "headline": story["title"],
            "slug": story["slug"],
            "content": story["content"],
            "body": story["content"],
            "cover_image": story["coverImage"],
            "image_url": story["coverImage"],
            "tags": story["tags"],
            "status": story["status"],
            "published_at": story["publishedAt"],
            "created_at": story["createdAt"],
            "updated_at": story["updatedAt"],
            "excerpt": story.get("excerpt", ""),
            "meta_description": story.get("metaDescription", ""),
            "category": story.get("category", ""),
            "quality_score": story.get("qualityScore"),
        }
    )
    return existing


def validate_create(data):
    if not isinstance(data, dict):
        raise StoryValidationError("Invalid JSON body")

    title = data.get("title", data.get("headline"))
    content = data.get("content", data.get("body"))
    cover_image = data.get("coverImage", data.get("image_url"))

    if not isinstance(title, str) or not title.strip():
        raise StoryValidationError("title is required and cannot be empty")
    if not isinstance(content, str) or not content.strip():
        raise StoryValidationError("content is required and cannot be empty")
    if not isinstance(cover_image, str) or not cover_image.strip():
        raise StoryValidationError("coverImage is required and cannot be empty")
    validate_image_url(cover_image.strip())

    status = normalize_status(data.get("status"), DRAFT)

    return {
        "title": title.strip(),
        "content": content.strip(),
        "coverImage": cover_image.strip(),
        "tags": normalize_tags(data.get("tags", [])),
        "slug": slugify(data.get("slug") or title),
        "status": status,
        "excerpt": str(data.get("excerpt", "")).strip(),
        "metaDescription": str(data.get("metaDescription", data.get("meta_description", ""))).strip(),
        "category": str(data.get("category", "")).strip(),
        "qualityScore": coerce_quality_score(data.get("qualityScore", data.get("quality_score"))),
    }


def validate_update(data):
    if not isinstance(data, dict) or not data:
        raise StoryValidationError("Invalid JSON body")

    aliases = {
        "headline": "title",
        "body": "content",
        "image_url": "coverImage",
        "cover_image": "coverImage",
        "meta_description": "metaDescription",
        "quality_score": "qualityScore",
    }
    normalized_data = {aliases.get(key, key): value for key, value in data.items()}
    allowed = {
        "title",
        "content",
        "coverImage",
        "tags",
        "excerpt",
        "metaDescription",
        "category",
        "qualityScore",
        "status",
        "slug",
    }
    unknown = set(normalized_data) - allowed
    if unknown:
        raise StoryValidationError(f"Unsupported fields: {', '.join(sorted(unknown))}")

    result = {}
    for field in ("title", "content", "coverImage"):
        if field in normalized_data:
            if not isinstance(normalized_data[field], str) or not normalized_data[field].strip():
                raise StoryValidationError(f"{field} cannot be empty")
            result[field] = normalized_data[field].strip()
            if field == "coverImage":
                validate_image_url(result[field])
    if "tags" in normalized_data:
        result["tags"] = normalize_tags(normalized_data["tags"])
    for field in ("excerpt", "metaDescription", "category"):
        if field in normalized_data:
            if normalized_data[field] is None:
                result[field] = ""
            elif not isinstance(normalized_data[field], str):
                raise StoryValidationError(f"{field} must be a string")
            else:
                result[field] = normalized_data[field].strip()
    if "qualityScore" in normalized_data:
        result["qualityScore"] = coerce_quality_score(normalized_data["qualityScore"])
    if "status" in normalized_data:
        result["status"] = normalize_status(normalized_data["status"])
    if "slug" in normalized_data and normalized_data["slug"]:
        if not isinstance(normalized_data["slug"], str):
            raise StoryValidationError("slug must be a string")
        result["slug"] = slugify(normalized_data["slug"])
    return result


def coerce_quality_score(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise StoryValidationError("qualityScore must be a number")


class LocalStoryRepository:
    def __init__(self, load, save):
        self.load = load
        self.save = save

    def list(self):
        return self.load()

    def get(self, story_id):
        return next(
            (
                story
                for story in self.load()
                if str(story.get("id")) == str(story_id) or story.get("slug") == story_id
            ),
            None,
        )

    def insert(self, story):
        stories = self.load()
        stories.append(story)
        self.save(stories)
        return story

    def update(self, story_id, story):
        stories = self.load()
        for index, current in enumerate(stories):
            if str(current.get("id")) == str(story_id):
                stories[index] = story
                self.save(stories)
                return story
        return None

    def delete(self, story_id):
        stories = self.load()
        remaining = [story for story in stories if str(story.get("id")) != str(story_id)]
        if len(remaining) == len(stories):
            return False
        self.save(remaining)
        return True


class SupabaseStoryRepository:
    def __init__(self, client):
        self.client = client

    def list(self):
        return self.client.table("stories").select("*").execute().data or []

    def get(self, story_id):
        response = self.client.table("stories").select("*").eq("id", story_id).execute()
        if not response.data:
            response = self.client.table("stories").select("*").eq("slug", story_id).execute()
        return response.data[0] if response.data else None

    def insert(self, story):
        response = self.client.table("stories").insert(story).execute()
        return response.data[0] if response.data else story

    def update(self, story_id, story):
        response = self.client.table("stories").update(story).eq("id", story_id).execute()
        return response.data[0] if response.data else None

    def delete(self, story_id):
        response = self.client.table("stories").delete().eq("id", story_id).execute()
        return bool(response.data)


class StoryService:
    def __init__(self, repository, id_factory):
        self.repository = repository
        self.id_factory = id_factory

    def list_admin(self):
        return [
            canonical_story(story)
            for story in sorted(
                self.repository.list(),
                key=lambda item: item.get("updated_at") or item.get("created_at") or "",
                reverse=True,
            )
        ]

    def list_public(self):
        stories = [story for story in self.repository.list() if is_public_status(story.get("status"))]
        stories.sort(
            key=lambda item: item.get("published_at") or item.get("created_at") or "",
            reverse=True,
        )
        return [public_story(story) for story in stories]

    def get_public(self, identifier):
        story = self.repository.get(identifier)
        if not story or not is_public_status(story.get("status")):
            raise StoryNotFoundError("Story not found")
        return public_story(story)

    def create(self, data):
        validated = validate_create(data)
        validated["slug"] = self._unique_slug(validated["slug"])
        now = utc_now()
        story = {
            "id": self.id_factory(),
            **validated,
            "publishedAt": now if validated["status"] == PUBLISHED else None,
            "createdAt": now,
            "updatedAt": now,
        }
        inserted = self.repository.insert(storage_story(story))
        return canonical_story(inserted)

    def update(self, identifier, data):
        current = self._get(identifier)
        changes = validate_update(data)
        story = canonical_story(current)
        story.update(changes)
        if "title" in changes and changes["title"] != canonical_story(current)["title"]:
            story["slug"] = self._unique_slug(slugify(changes["title"]), story["id"])
        elif "slug" in changes:
            story["slug"] = self._unique_slug(changes["slug"], story["id"])
        story["updatedAt"] = utc_now()
        if "status" in changes:
            if changes["status"] == PUBLISHED and not story.get("publishedAt"):
                story["publishedAt"] = story["updatedAt"]
            elif changes["status"] == DRAFT:
                story["publishedAt"] = None
        updated = self.repository.update(story["id"], storage_story(story, current))
        return canonical_story(updated)

    def set_status(self, identifier, status):
        status = normalize_status(status)
        current = self._get(identifier)
        story = canonical_story(current)
        now = utc_now()
        story["status"] = status
        story["publishedAt"] = now if status == PUBLISHED else None
        story["updatedAt"] = now
        updated = self.repository.update(story["id"], storage_story(story, current))
        return canonical_story(updated)

    def delete(self, identifier):
        current = self._get(identifier)
        if not self.repository.delete(str(current.get("id"))):
            raise StoryNotFoundError("Story not found")

    def _get(self, identifier):
        story = self.repository.get(identifier)
        if not story:
            raise StoryNotFoundError("Story not found")
        return story

    def _unique_slug(self, base_slug, current_id=None):
        existing = {
            story.get("slug")
            for story in self.repository.list()
            if str(story.get("id")) != str(current_id)
        }
        if base_slug not in existing:
            return base_slug

        suffix = 2
        while f"{base_slug}-{suffix}" in existing:
            suffix += 1
        return f"{base_slug}-{suffix}"
