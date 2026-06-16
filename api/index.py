import os
import json
import time
import random
import string
import re
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client
from api.story_service import (
    DRAFT,
    LocalStoryRepository,
    PUBLISHED,
    StoryNotFoundError,
    StoryService,
    StoryValidationError,
    SupabaseStoryRepository,
)
from api.comment_service import (
    APPROVED,
    PENDING_REVIEW,
    REJECTED,
    SPAM,
    CommentNotFoundError,
    CommentService,
    CommentValidationError,
    LocalCommentRepository,
    SupabaseCommentRepository,
    atomic_json_save,
)

load_dotenv()

app = Flask(__name__, static_folder='..')
CORS(app)

def get_supabase_client():
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")
    
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def generate_id():
    now_ms = int(time.time() * 1000)
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{now_ms}-{random_str}"

def normalize_story(input_data):
    headline = str(input_data.get('headline', ''))
    body = str(input_data.get('body', ''))
    excerpt = str(input_data.get('excerpt', ''))
    image_url = str(input_data.get('image_url', ''))
    tags = input_data.get('tags', [])
    slug = str(input_data.get('slug', ''))
    meta_description = str(input_data.get('meta_description', ''))
    category = str(input_data.get('category', ''))
    quality_score = input_data.get('quality_score')
    status = str(input_data.get('status', ''))

    normalized_tags = []
    if isinstance(tags, list):
        for t in tags:
            if not isinstance(t, str):
                continue
            # support a single list element that contains comma-separated tags
            parts = [p.strip() for p in t.split(',') if p.strip()]
            normalized_tags.extend(parts)
        # de-duplicate while preserving order
        seen = set()
        deduped = []
        for t in normalized_tags:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        normalized_tags = deduped

    if quality_score is not None:
        try:
            quality_score = float(quality_score)
        except (ValueError, TypeError):
            quality_score = None

    if not slug:
        slug = headline.lower()
        slug = re.sub(r'\s+', '-', slug)
        slug = re.sub(r'[^a-z0-9\-]', '', slug)

    now = datetime.now(timezone.utc).isoformat()
    
    return {
        "id": generate_id(),
        "headline": headline,
        "body": body,
        "excerpt": excerpt,
        "image_url": image_url,
        "tags": normalized_tags,
        "slug": slug,
        "meta_description": meta_description,
        "category": category,
        "quality_score": quality_score,
        "status": status,
        "created_at": now
    }

def is_supabase_configured():
    return bool(os.environ.get('SUPABASE_URL') and os.environ.get('SUPABASE_KEY'))

COMMENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'comments.json')
STORIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data.json')

def load_local_stories():
    if os.path.exists(STORIES_FILE):
        try:
            with open(STORIES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading local stories: {e}")
    return []

def save_local_stories(stories):
    try:
        with open(STORIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(stories, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing local stories: {e}")


def get_story_service():
    if is_supabase_configured():
        repository = SupabaseStoryRepository(get_supabase_client())
    else:
        repository = LocalStoryRepository(load_local_stories, save_local_stories)
    return StoryService(repository, generate_id)


def is_admin_authorized():
    expected = os.environ.get('ADMIN_TOKEN')
    return bool(expected and request.headers.get('x-admin-token') == expected)


def get_comment_service():
    if is_supabase_configured():
        repository = SupabaseCommentRepository(get_supabase_client())
    else:
        repository = LocalCommentRepository(load_local_comments, save_local_comments)
    return CommentService(repository, generate_id)


def get_story_comment_aliases(story_id):
    for story in get_story_service().repository.list():
        if str(story.get("id")) == str(story_id) or story.get("slug") == story_id:
            return [story.get("id"), story.get("slug")]
    return []


def load_local_comments():
    if os.path.exists(COMMENTS_FILE):
        try:
            with open(COMMENTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading local comments: {e}")
    return []

def save_local_comments(comments):
    try:
        atomic_json_save(COMMENTS_FILE, comments, json)
    except Exception as e:
        print(f"Error writing local comments: {e}")
        raise

def normalize_comment(input_data):
    path = str(input_data.get('path', '')).strip()
    author = str(input_data.get('author', '')).strip()
    email = str(input_data.get('email', '')).strip()
    url = str(input_data.get('url', '')).strip()
    text = str(input_data.get('text', '')).strip()

    if not path:
        raise ValueError('Missing required field: path')
    if not text:
        raise ValueError('Missing required field: text')

    if len(path) > 200:
        raise ValueError('path must be 200 characters or fewer')
    if len(author) > 120:
        raise ValueError('author must be 120 characters or fewer')
    if len(email) > 200:
        raise ValueError('email must be 200 characters or fewer')
    if len(url) > 500:
        raise ValueError('url must be 500 characters or fewer')
    if len(text) > 4000:
        raise ValueError('text must be 4000 characters or fewer')

    return {
        'path': path,
        'author': author if author else None,
        'email': email if email else None,
        'url': url if url else None,
        'text': text,
        'approved': False,
    }

@app.route('/')
@app.route('/index.html')
def serve_index():
    return send_from_directory('..', 'index.html')

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"ok": True})

@app.route('/api/stories', methods=['GET'])
def get_stories(top_n: int = None) -> dict:
    try:
        stories = get_story_service().list_public()
        filters_param = request.args.get('filters', '')
        filters = [value.strip().lower() for value in filters_param.split(',') if value.strip()]
        sort_by_popular = 'popular' in filters
        filters = [value for value in filters if value != 'popular']

        if filters:
            stories = [
                story for story in stories
                if str(story.get('category', '')).lower() in filters
                or any(
                    filter_value in str(tag).lower()
                    for filter_value in filters
                    for tag in story.get('tags', [])
                )
            ]
        if sort_by_popular:
            stories.sort(key=lambda story: story.get('quality_score') or 0, reverse=True)

        top_n_param = request.args.get('top_n')
        if top_n_param is not None:
            try:
                top_n = int(top_n_param)
                if top_n >= 0:
                    stories = stories[:top_n]
            except ValueError:
                pass
        return jsonify(stories)

        # Legacy implementation retained below for reference; the service return above
        # centralizes published-only filtering across local and Supabase storage.
        # Extract optional top_n from query params, default to None (return all)
        top_n_param = request.args.get('top_n')
        if top_n_param is not None:
            try:
                top_n = int(top_n_param)
            except ValueError:
                top_n = None  # fallback to all if invalid
        else:
            top_n = None  # return all if empty

        # Support filters via query param `filters` (comma-separated).
        # Special filter `popular` sorts by `quality_score` desc.
        filters_param = request.args.get('filters')
        filters = []
        sort_by_popular = False
        if filters_param:
            filters = [f.strip().lower() for f in filters_param.split(',') if f.strip()]
            if 'popular' in filters:
                sort_by_popular = True
                filters = [f for f in filters if f != 'popular']

        if is_supabase_configured():
            supabase = get_supabase_client()
            if sort_by_popular:
                query = supabase.table('stories').select('*').order('quality_score', desc=True)
            else:
                query = supabase.table('stories').select('*').order('created_at', desc=False)

            if top_n is not None:
                query = query.limit(top_n)

            response = query.execute()

            stories = response.data or []

            # If filters specified, prefer DB-level filtering per-filter (category eq OR tags contains).
            if filters:
                collected = []
                seen_ids = set()
                for f in filters:
                    # try category match
                    try:
                        q_cat = supabase.table('stories').select('*').eq('category', f)
                        if sort_by_popular:
                            q_cat = q_cat.order('quality_score', desc=True)
                        else:
                            q_cat = q_cat.order('created_at', desc=False)
                        # fetch a reasonable batch
                        q_cat = q_cat.limit(top_n * 3 if top_n else 100)
                        resp_cat = q_cat.execute()
                        for s in (resp_cat.data or []):
                            sid = s.get('id')
                            if sid and sid not in seen_ids:
                                seen_ids.add(sid)
                                collected.append(s)
                    except Exception:
                        pass

                    # try tags contains (for text[] or json array column)
                    try:
                        q_tags = supabase.table('stories').select('*').filter('tags', 'cs', [f])
                        if sort_by_popular:
                            q_tags = q_tags.order('quality_score', desc=True)
                        else:
                            q_tags = q_tags.order('created_at', desc=False)
                        q_tags = q_tags.limit(top_n * 3 if top_n else 100)
                        resp_tags = q_tags.execute()
                        for s in (resp_tags.data or []):
                            sid = s.get('id')
                            if sid and sid not in seen_ids:
                                seen_ids.add(sid)
                                collected.append(s)
                    except Exception:
                        # fallback: skip tags DB filter if unsupported
                        pass

                # If DB-level collected nothing, fall back to in-memory filtering of the original fetch
                if not collected:
                    def matches_filters_local(story, filters_list):
                        if not filters_list:
                            return True
                        cat = str(story.get('category', '')).lower()
                        tags_val = story.get('tags') or []
                        normalized_tags = []
                        if isinstance(tags_val, list):
                            for t in tags_val:
                                if not isinstance(t, str):
                                    continue
                                for part in [p.strip() for p in t.split(',') if p.strip()]:
                                    normalized_tags.append(part.lower())
                        elif isinstance(tags_val, str):
                            normalized_tags = [p.strip().lower() for p in tags_val.split(',') if p.strip()]

                        for f in filters_list:
                            if f == cat:
                                return True
                            for t in normalized_tags:
                                if f in t:
                                    return True
                        return False

                    collected = [s for s in stories if matches_filters_local(s, filters)]

                # apply ordering
                if sort_by_popular:
                    try:
                        collected.sort(key=lambda s: (s.get('quality_score') is None, -(s.get('quality_score') or 0)))
                    except Exception:
                        pass
                else:
                    try:
                        collected.sort(key=lambda s: s.get('created_at') or '')
                    except Exception:
                        pass

                # apply final limit
                if top_n is not None:
                    collected = collected[:top_n]

                return jsonify(collected)

            # no filters: return DB response (possibly limited above)
            return jsonify(stories)
        else:
            # Local fallback mode
            stories = load_local_stories()

            if filters:
                def matches_filters_local(story, filters_list):
                    if not filters_list:
                        return True
                    cat = str(story.get('category', '')).lower()
                    tags_val = story.get('tags') or []
                    normalized_tags = []
                    if isinstance(tags_val, list):
                        for t in tags_val:
                            if not isinstance(t, str):
                                continue
                            for part in [p.strip() for p in t.split(',') if p.strip()]:
                                normalized_tags.append(part.lower())
                    elif isinstance(tags_val, str):
                        normalized_tags = [p.strip().lower() for p in tags_val.split(',') if p.strip()]

                    for f in filters_list:
                        if f == cat:
                            return True
                        for t in normalized_tags:
                            if f in t:
                                return True
                    return False

                stories = [s for s in stories if matches_filters_local(s, filters)]

            if sort_by_popular:
                try:
                    stories.sort(key=lambda s: (s.get('quality_score') is None, -(s.get('quality_score') or 0)))
                except Exception:
                    pass
            else:
                try:
                    stories.sort(key=lambda s: s.get('created_at') or '')
                except Exception:
                    pass

            if top_n is not None:
                stories = stories[:top_n]

            return jsonify(stories)
    except Exception as e:
        print(f"Error fetching stories: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stories/<slug>', methods=['GET'])
def get_story(slug):
    try:
        return jsonify(get_story_service().get_public(slug))

        if is_supabase_configured():
            supabase = get_supabase_client()
            response = supabase.table('stories').select('*').eq('slug', slug).execute()
            if not response.data:
                return jsonify({"error": "Not found"}), 404
            return jsonify(response.data[0])
        else:
            stories = load_local_stories()
            for s in stories:
                if s.get('slug') == slug or str(s.get('id')) == slug:
                    return jsonify(s)
            return jsonify({"error": "Not found"}), 404
    except StoryNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error fetching story: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/comments', methods=['GET'])
def get_comments():
    try:
        path = str(request.args.get('path', '')).strip()
        if not path:
            return jsonify({"error": "Missing required query param: path"}), 400
        story_id = path[6:] if path.startswith("story:") else path
        return jsonify(get_comment_service().list_public(story_id, get_story_comment_aliases(story_id)))
    except Exception as e:
        print(f"Error fetching comments: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/stories/<story_id>/comments', methods=['GET'])
def get_story_comments(story_id):
    try:
        return jsonify(get_comment_service().list_public(story_id, get_story_comment_aliases(story_id)))
    except Exception as e:
        print(f"Error fetching story comments: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/comments', methods=['POST'])
def create_comment():
    try:
        data = request.get_json(silent=True)
        if isinstance(data, dict) and not data.get("story_id") and data.get("path"):
            path = str(data["path"]).strip()
            data = {**data, "story_id": path[6:] if path.startswith("story:") else path}
        comment = get_comment_service().create(data)
        return jsonify({
            "success": True,
            "status": PENDING_REVIEW,
            "message": "Thanks. Your comment is awaiting moderation.",
            "comment": comment,
        }), 201
    except CommentValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error creating comment: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/stories', methods=['POST'])
def create_story():
    try:
        if not is_admin_authorized():
            return jsonify({"error": "Forbidden"}), 403

        return jsonify(get_story_service().create(request.get_json(silent=True))), 201
    except StoryValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error creating story: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/stories', methods=['DELETE'])
def delete_stories():
    try:
        if not is_admin_authorized():
            return jsonify({"error": "Forbidden"}), 403

        if is_supabase_configured():
            supabase = get_supabase_client()
            response = supabase.table('stories').select('id').execute()
            count = len(response.data)
            if count > 0:
                ids = [s['id'] for s in response.data]
                supabase.table('stories').delete().in_('id', ids).execute()
            return jsonify({"ok": True, "deleted": count})
        else:
            stories = load_local_stories()
            count = len(stories)
            save_local_stories([])
            return jsonify({"ok": True, "deleted": count})
    except Exception as e:
        print(f"Error deleting stories: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/reset', methods=['POST', 'DELETE'])
def reset_stories():
    try:
        token = request.headers.get('x-admin-token')
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected or token != expected:
            return jsonify({"error": "Forbidden"}), 403
        
        if is_supabase_configured():
            supabase = get_supabase_client()
            response = supabase.table('stories').select('id').execute()
            count = len(response.data)
            if count > 0:
                ids = [s['id'] for s in response.data]
                supabase.table('stories').delete().in_('id', ids).execute()
            return jsonify({"ok": True, "deleted": count})
        else:
            stories = load_local_stories()
            count = len(stories)
            save_local_stories([])
            return jsonify({"ok": True, "deleted": count})
    except Exception as e:
        print(f"Error resetting stories: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/normalize-tags', methods=['POST'])
def admin_normalize_tags():
    try:
        token = request.headers.get('x-admin-token')
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected or token != expected:
            return jsonify({"error": "Forbidden"}), 403

        def normalize_tags_list(tags_val):
            parts = []
            if isinstance(tags_val, list):
                for t in tags_val:
                    if not isinstance(t, str):
                        continue
                    for p in [p.strip() for p in t.split(',') if p.strip()]:
                        parts.append(p)
            elif isinstance(tags_val, str):
                parts = [p.strip() for p in tags_val.split(',') if p.strip()]

            seen = set()
            out = []
            for p in parts:
                if p not in seen:
                    seen.add(p)
                    out.append(p)
            return out

        if is_supabase_configured():
            supabase = get_supabase_client()
            response = supabase.table('stories').select('id,tags').execute()
            rows = response.data or []
            updated = 0

            for r in rows:
                sid = r.get('id')
                tags_val = r.get('tags')
                normalized = normalize_tags_list(tags_val)
                if normalized != tags_val:
                    supabase.table('stories').update({'tags': normalized}).eq('id', sid).execute()
                    updated += 1

            return jsonify({"ok": True, "updated": updated})
        else:
            stories = load_local_stories()
            updated = 0
            for s in stories:
                tags_val = s.get('tags')
                normalized = normalize_tags_list(tags_val)
                if normalized != tags_val:
                    s['tags'] = normalized
                    updated += 1
            if updated > 0:
                save_local_stories(stories)
            return jsonify({"ok": True, "updated": updated})
    except Exception as e:
        print(f"Error normalizing tags: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/comments', methods=['GET'])
def admin_get_comments():
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        return jsonify(get_comment_service().list_admin(request.args.get("status")))
    except CommentValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error fetching admin comments: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/comments/moderate', methods=['POST'])
def admin_moderate_comment():
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        data = request.get_json(silent=True)
        if not data or 'id' not in data or 'action' not in data:
            return jsonify({"error": "Missing id or action"}), 400

        action = data["action"]
        if action == "purge":
            get_comment_service().delete(data["id"])
            return jsonify({"success": True})
        status_by_action = {
            "approve": APPROVED,
            "reject": REJECTED,
            "spam": SPAM,
            "pending": PENDING_REVIEW,
        }
        if action not in status_by_action:
            return jsonify({"error": f"Invalid action: {action}"}), 400
        comment = get_comment_service().set_status(data["id"], status_by_action[action])
        return jsonify({"ok": True, "comment": comment})
    except CommentNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error moderating comment: {e}")
        return jsonify({"error": "Internal server error"}), 500


def moderate_comment(comment_id, status):
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        return jsonify(get_comment_service().set_status(comment_id, status))
    except CommentNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error moderating comment: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/comments/<comment_id>/approve', methods=['PATCH'])
def admin_approve_comment(comment_id):
    return moderate_comment(comment_id, APPROVED)


@app.route('/api/admin/comments/<comment_id>/reject', methods=['PATCH'])
def admin_reject_comment(comment_id):
    return moderate_comment(comment_id, REJECTED)


@app.route('/api/admin/comments/<comment_id>/spam', methods=['PATCH'])
def admin_spam_comment(comment_id):
    return moderate_comment(comment_id, SPAM)


@app.route('/api/admin/comments/<comment_id>/pending', methods=['PATCH'])
def admin_pending_comment(comment_id):
    return moderate_comment(comment_id, PENDING_REVIEW)


@app.route('/api/admin/comments/<comment_id>', methods=['DELETE'])
def admin_delete_comment(comment_id):
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        get_comment_service().delete(comment_id)
        return jsonify({"success": True})
    except CommentNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error deleting comment: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/stories/<id>', methods=['DELETE'])
def delete_story(id):
    try:
        if not is_admin_authorized():
            return jsonify({"error": "Forbidden"}), 403

        aliases = get_story_comment_aliases(id)
        get_story_service().delete(id)
        get_comment_service().delete_for_story(id, aliases)
        return jsonify({"success": True})
    except StoryNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error deleting story: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/stories/<id>', methods=['PUT'])
def update_story(id):
    try:
        if not is_admin_authorized():
            return jsonify({"error": "Forbidden"}), 403

        return jsonify(get_story_service().update(id, request.get_json(silent=True))), 200
    except StoryValidationError as e:
        return jsonify({"error": str(e)}), 400
    except StoryNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        print(f"Error updating story: {e}")
        return jsonify({"error": "Internal server error"}), 500


def admin_story_error(error):
    if isinstance(error, StoryValidationError):
        return jsonify({"error": str(error)}), 400
    if isinstance(error, StoryNotFoundError):
        return jsonify({"error": str(error)}), 404
    print(f"Admin story error: {error}")
    return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/stories', methods=['GET'])
def admin_list_stories():
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        return jsonify(get_story_service().list_admin())
    except Exception as error:
        return admin_story_error(error)


@app.route('/api/admin/stories', methods=['POST'])
def admin_create_story():
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        return jsonify(get_story_service().create(request.get_json(silent=True))), 201
    except Exception as error:
        return admin_story_error(error)


@app.route('/api/admin/stories/<story_id>/publish', methods=['PATCH'])
def admin_publish_story(story_id):
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        return jsonify(get_story_service().set_status(story_id, PUBLISHED))
    except Exception as error:
        return admin_story_error(error)


@app.route('/api/admin/stories/<story_id>/draft', methods=['PATCH'])
def admin_draft_story(story_id):
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        return jsonify(get_story_service().set_status(story_id, DRAFT))
    except Exception as error:
        return admin_story_error(error)


@app.route('/api/admin/stories/<story_id>', methods=['PUT'])
def admin_update_story(story_id):
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        return jsonify(get_story_service().update(story_id, request.get_json(silent=True)))
    except Exception as error:
        return admin_story_error(error)


@app.route('/api/admin/stories/<story_id>', methods=['DELETE'])
def admin_delete_story(story_id):
    if not is_admin_authorized():
        return jsonify({"error": "Forbidden"}), 403
    try:
        aliases = get_story_comment_aliases(story_id)
        get_story_service().delete(story_id)
        get_comment_service().delete_for_story(story_id, aliases)
        return jsonify({"success": True})
    except Exception as error:
        return admin_story_error(error)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port)
