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

    # now = datetime.now(timezone.utc).isoformat()
    
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
        "status": status
    }

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
        'author': author,
        'email': email,
        'url': url,
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
        supabase = get_supabase_client()
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

        # Build initial query: order by quality_score if popular, otherwise by created_at
        if sort_by_popular:
            query = supabase.table('stories').select('*').order('quality_score', desc=True)
        else:
            query = supabase.table('stories').select('*').order('created_at', desc=True)

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
                        q_cat = q_cat.order('created_at', desc=True)
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
                        q_tags = q_tags.order('created_at', desc=True)
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
                    collected.sort(key=lambda s: s.get('created_at') or '', reverse=True)
                except Exception:
                    pass

            # apply final limit
            if top_n is not None:
                collected = collected[:top_n]

            return jsonify(collected)

        # no filters: return DB response (possibly limited above)
        return jsonify(stories)
    except Exception as e:
        print(f"Error fetching stories: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stories/<slug>', methods=['GET'])
def get_story(slug):
    try:
        supabase = get_supabase_client()
        response = supabase.table('stories').select('*').eq('slug', slug).execute()
        if not response.data:
            return jsonify({"error": "Not found"}), 404
        return jsonify(response.data[0])
    except Exception as e:
        print(f"Error fetching story: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/comments', methods=['GET'])
def get_comments():
    try:
        path = str(request.args.get('path', '')).strip()
        if not path:
            return jsonify({"error": "Missing required query param: path"}), 400

        supabase = get_supabase_client()
        paths = [path]
        if path.startswith('story:'):
            legacy_path = path.split('story:', 1)[1].strip()
            if legacy_path:
                paths.append(legacy_path)

        collected = []
        seen_ids = set()
        for candidate_path in paths:
            response = (
                supabase.table('comments')
                .select('id,path,author,url,text,created_at')
                .eq('path', candidate_path)
                .eq('approved', True)
                .order('created_at', desc=False)
                .execute()
            )
            for comment in response.data or []:
                comment_id = comment.get('id')
                if comment_id in seen_ids:
                    continue
                seen_ids.add(comment_id)
                collected.append(comment)

        return jsonify(collected)
    except Exception as e:
        print(f"Error fetching comments: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/comments', methods=['POST'])
def create_comment():
    try:
        supabase = get_supabase_client()
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400

        comment = normalize_comment(data)
        response = supabase.table('comments').insert(comment).execute()
        inserted = response.data[0] if response.data else comment
        return jsonify({
            "message": "Thanks. Your comment is awaiting moderation.",
            "comment": inserted,
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error creating comment: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stories', methods=['POST'])
def create_story():
    try:
        supabase = get_supabase_client()
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400
        
        required = ['headline', 'body', 'image_url']
        for field in required:
            if not data.get(field):
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        if 'tags' in data and not isinstance(data['tags'], list):
            return jsonify({"error": "tags must be an array"}), 400
            
        story = normalize_story(data)
        response = supabase.table('stories').insert(story).execute()
        return jsonify(response.data[0]), 201
    except Exception as e:
        print(f"Error creating story: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stories', methods=['DELETE'])
def delete_stories():
    try:
        supabase = get_supabase_client()
        response = supabase.table('stories').select('id').execute()
        count = len(response.data)
        if count > 0:
            ids = [s['id'] for s in response.data]
            supabase.table('stories').delete().in_('id', ids).execute()
        return jsonify({"ok": True, "deleted": count})
    except Exception as e:
        print(f"Error deleting stories: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/reset', methods=['POST', 'DELETE'])
def reset_stories():
    try:
        supabase = get_supabase_client()
        token = request.headers.get('x-admin-token')
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected or token != expected:
            return jsonify({"error": "Forbidden"}), 403
        
        response = supabase.table('stories').select('id').execute()
        count = len(response.data)
        if count > 0:
            ids = [s['id'] for s in response.data]
            supabase.table('stories').delete().in_('id', ids).execute()
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

        supabase = get_supabase_client()
        response = supabase.table('stories').select('id,tags').execute()
        rows = response.data or []
        updated = 0

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

        for r in rows:
            sid = r.get('id')
            tags_val = r.get('tags')
            normalized = normalize_tags_list(tags_val)
            if normalized != tags_val:
                supabase.table('stories').update({'tags': normalized}).eq('id', sid).execute()
                updated += 1

        return jsonify({"ok": True, "updated": updated})
    except Exception as e:
        print(f"Error normalizing tags: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    try:
        data = request.get_json() or {}
        expected_user = os.environ.get('ADMIN_USER')
        expected_pass = os.environ.get('ADMIN_PASS')
        token = os.environ.get('ADMIN_TOKEN')

        if not expected_user or not expected_pass or not token:
            return jsonify({"error": "Admin credentials not configured"}), 500

        if str(data.get('username', '')) == expected_user and str(data.get('password', '')) == expected_pass:
            return jsonify({"token": token})
        return jsonify({"error": "Invalid credentials"}), 403
    except Exception as e:
        print(f"Error in admin_login: {e}")
        return jsonify({"error": str(e)}), 500


def _check_admin_token():
    token = request.headers.get('x-admin-token')
    expected = os.environ.get('ADMIN_TOKEN')
    if not expected or token != expected:
        return False
    return True


@app.route('/api/admin/stories', methods=['POST'])
def admin_create_story():
    try:
        if not _check_admin_token():
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400

        if 'tags' in data and not isinstance(data['tags'], list):
            return jsonify({"error": "tags must be an array"}), 400

        story = normalize_story(data)
        supabase = get_supabase_client()
        response = supabase.table('stories').insert(story).execute()
        return jsonify(response.data[0] if response.data else story), 201
    except Exception as e:
        print(f"Error in admin_create_story: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/comments/<int:comment_id>', methods=['PATCH'])
def admin_update_comment(comment_id):
    try:
        if not _check_admin_token():
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json() or {}
        if 'approved' not in data:
            return jsonify({"error": "Nothing to update"}), 400

        supabase = get_supabase_client()
        response = supabase.table('comments').update({'approved': bool(data.get('approved'))}).eq('id', comment_id).execute()
        return jsonify(response.data[0] if response.data else {"ok": True}), 200
    except Exception as e:
        print(f"Error updating comment: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port)