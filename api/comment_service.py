import os
import re
import tempfile
from datetime import datetime, timezone


PENDING_REVIEW = "PENDING_REVIEW"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
SPAM = "SPAM"
VALID_COMMENT_STATUSES = {PENDING_REVIEW, APPROVED, REJECTED, SPAM}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CommentValidationError(ValueError):
    pass


class CommentNotFoundError(LookupError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical_comment(record):
    path = str(record.get("path") or "")
    story_id = str(record.get("story_id") or "")
    if not story_id and path.startswith("story:"):
        story_id = path[6:]

    status = record.get("status")
    if status not in VALID_COMMENT_STATUSES:
        status = APPROVED if record.get("approved") is True else PENDING_REVIEW

    created_at = record.get("created_at")
    return {
        "id": str(record.get("id", "")),
        "story_id": story_id,
        "author_name": record.get("author_name", record.get("author")) or "",
        "author_email": record.get("author_email", record.get("email")) or "",
        "content": record.get("content", record.get("text")) or "",
        "url": record.get("url") or "",
        "status": status,
        "created_at": created_at,
        "updated_at": record.get("updated_at") or created_at,
    }


def public_comment(record):
    comment = canonical_comment(record)
    return {
        "id": comment["id"],
        "story_id": comment["story_id"],
        "author_name": comment["author_name"],
        "content": comment["content"],
        "status": comment["status"],
        "created_at": comment["created_at"],
        "updated_at": comment["updated_at"],
        "author": comment["author_name"],
        "text": comment["content"],
    }


def storage_comment(comment, existing=None):
    existing = dict(existing or {})
    existing.update(
        {
            "id": comment["id"],
            "story_id": comment["story_id"],
            "path": f"story:{comment['story_id']}",
            "author_name": comment["author_name"],
            "author": comment["author_name"],
            "author_email": comment["author_email"],
            "email": comment["author_email"],
            "content": comment["content"],
            "text": comment["content"],
            "url": comment.get("url") or "",
            "status": comment["status"],
            "approved": comment["status"] == APPROVED,
            "created_at": comment["created_at"],
            "updated_at": comment["updated_at"],
        }
    )
    return existing


def validate_new_comment(data):
    if not isinstance(data, dict):
        raise CommentValidationError("Invalid JSON body")

    story_id = data.get("story_id")
    author_name = data.get("author_name", data.get("author"))
    author_email = data.get("author_email", data.get("email"))
    content = data.get("content", data.get("text"))
    url = data.get("url", "")

    if not isinstance(story_id, str) or not story_id.strip():
        raise CommentValidationError("story_id is required")
    if not isinstance(author_name, str) or not author_name.strip():
        raise CommentValidationError("author_name is required and cannot be empty")
    if not isinstance(author_email, str) or not EMAIL_PATTERN.fullmatch(author_email.strip()):
        raise CommentValidationError("author_email must be a valid email address")
    if not isinstance(content, str) or not content.strip():
        raise CommentValidationError("content is required and cannot be empty")
    if "status" in data and data["status"] not in VALID_COMMENT_STATUSES:
        raise CommentValidationError("Invalid status")

    if len(story_id.strip()) > 200:
        raise CommentValidationError("story_id must be 200 characters or fewer")
    if len(author_name.strip()) > 120:
        raise CommentValidationError("author_name must be 120 characters or fewer")
    if len(author_email.strip()) > 200:
        raise CommentValidationError("author_email must be 200 characters or fewer")
    if len(content.strip()) > 4000:
        raise CommentValidationError("content must be 4000 characters or fewer")
    if not isinstance(url, str) or len(url.strip()) > 500:
        raise CommentValidationError("url must be 500 characters or fewer")

    return {
        "story_id": story_id.strip(),
        "author_name": author_name.strip(),
        "author_email": author_email.strip(),
        "content": content.strip(),
        "url": url.strip(),
    }


class LocalCommentRepository:
    def __init__(self, load, save):
        self.load = load
        self.save = save

    def list(self):
        return self.load()

    def get(self, comment_id):
        return next(
            (comment for comment in self.load() if str(comment.get("id")) == str(comment_id)),
            None,
        )

    def insert(self, comment):
        comments = self.load()
        comments.append(comment)
        self.save(comments)
        return comment

    def update(self, comment_id, comment):
        comments = self.load()
        for index, current in enumerate(comments):
            if str(current.get("id")) == str(comment_id):
                comments[index] = comment
                self.save(comments)
                return comment
        return None

    def delete(self, comment_id):
        comments = self.load()
        remaining = [item for item in comments if str(item.get("id")) != str(comment_id)]
        if len(remaining) == len(comments):
            return False
        self.save(remaining)
        return True


class SupabaseCommentRepository:
    def __init__(self, client):
        self.client = client

    def list(self):
        return self.client.table("comments").select("*").execute().data or []

    def get(self, comment_id):
        response = self.client.table("comments").select("*").eq("id", comment_id).execute()
        return response.data[0] if response.data else None

    def insert(self, comment):
        payload = dict(comment)
        payload.pop("id", None)
        response = self.client.table("comments").insert(payload).execute()
        return response.data[0] if response.data else payload

    def update(self, comment_id, comment):
        payload = dict(comment)
        payload.pop("id", None)
        response = self.client.table("comments").update(payload).eq("id", comment_id).execute()
        return response.data[0] if response.data else None

    def delete(self, comment_id):
        response = self.client.table("comments").delete().eq("id", comment_id).execute()
        return bool(response.data)


class CommentService:
    def __init__(self, repository, id_factory):
        self.repository = repository
        self.id_factory = id_factory

    def create(self, data):
        validated = validate_new_comment(data)
        now = utc_now()
        comment = {
            "id": self.id_factory(),
            **validated,
            "status": PENDING_REVIEW,
            "created_at": now,
            "updated_at": now,
        }
        return canonical_comment(self.repository.insert(storage_comment(comment)))

    def list_public(self, story_id, aliases=None):
        identifiers = {str(story_id), *(str(alias) for alias in (aliases or []))}
        return [
            public_comment(comment)
            for comment in sorted(
                self.repository.list(),
                key=lambda item: item.get("created_at") or "",
            )
            if canonical_comment(comment)["story_id"] in identifiers
            and canonical_comment(comment)["status"] == APPROVED
        ]

    def list_admin(self, status=None):
        if status is not None and status not in VALID_COMMENT_STATUSES:
            raise CommentValidationError("Invalid status")
        comments = [canonical_comment(comment) for comment in self.repository.list()]
        if status:
            comments = [comment for comment in comments if comment["status"] == status]
        return sorted(comments, key=lambda item: item.get("created_at") or "", reverse=True)

    def set_status(self, comment_id, status):
        if status not in VALID_COMMENT_STATUSES:
            raise CommentValidationError("Invalid status")
        current = self._get(comment_id)
        comment = canonical_comment(current)
        comment["status"] = status
        comment["updated_at"] = utc_now()
        return canonical_comment(
            self.repository.update(comment["id"], storage_comment(comment, current))
        )

    def delete(self, comment_id):
        current = self._get(comment_id)
        if not self.repository.delete(current.get("id")):
            raise CommentNotFoundError("Comment not found")

    def _get(self, comment_id):
        comment = self.repository.get(comment_id)
        if not comment:
            raise CommentNotFoundError("Comment not found")
        return comment


def atomic_json_save(path, data, json_module):
    directory = os.path.dirname(path)
    descriptor, temp_path = tempfile.mkstemp(prefix=".comments-", suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json_module.dump(data, file, indent=2, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
