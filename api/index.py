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

def is_supabase_configured():
    return bool(os.environ.get('SUPABASE_URL') and os.environ.get('SUPABASE_KEY'))

COMMENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'comments.json')

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
        with open(COMMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(comments, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing local comments: {e}")

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

        if is_supabase_configured():
            supabase = get_supabase_client()
            response = (
                supabase.table('comments')
                .select('id,path,author,url,text,created_at')
                .eq('path', path)
                .eq('approved', True)
                .order('created_at', desc=False)
                .execute()
            )
            return jsonify(response.data or [])
        else:
            comments = load_local_comments()
            filtered = [
                c for c in comments 
                if c.get('path') == path and c.get('approved', True)
            ]
            try:
                filtered.sort(key=lambda x: x.get('created_at', ''))
            except Exception:
                pass
            return jsonify(filtered)
    except Exception as e:
        print(f"Error fetching comments: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/comments', methods=['POST'])
def create_comment():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400

        comment = normalize_comment(data)

        if is_supabase_configured():
            supabase = get_supabase_client()
            response = supabase.table('comments').insert(comment).execute()
            inserted = response.data[0] if response.data else comment
            return jsonify({
                "message": "Thanks. Your comment is awaiting moderation.",
                "comment": inserted,
            }), 201
        else:
            comment['id'] = generate_id()
            comment['created_at'] = datetime.now(timezone.utc).isoformat()
            comment['approved'] = True
            
            comments = load_local_comments()
            comments.append(comment)
            save_local_comments(comments)
            
            return jsonify({
                "message": "Thanks. Your comment has been posted.",
                "comment": comment,
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


@app.route('/api/admin/comments', methods=['GET'])
def admin_get_comments():
    try:
        token = request.headers.get('x-admin-token')
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected or token != expected:
            return jsonify({"error": "Forbidden"}), 403

        if is_supabase_configured():
            supabase = get_supabase_client()
            response = (
                supabase.table('comments')
                .select('*')
                .order('created_at', desc=True)
                .execute()
            )
            return jsonify(response.data or [])
        else:
            comments = load_local_comments()
            return jsonify(comments)
    except Exception as e:
        print(f"Error fetching admin comments: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/comments/moderate', methods=['POST'])
def admin_moderate_comment():
    try:
        token = request.headers.get('x-admin-token')
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected or token != expected:
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json()
        if not data or 'id' not in data or 'action' not in data:
            return jsonify({"error": "Missing id or action"}), 400

        comment_id = data['id']
        action = data['action'] # 'approve', 'reject', 'spam', 'purge'

        if is_supabase_configured():
            supabase = get_supabase_client()
            if action == 'approve':
                response = supabase.table('comments').update({'approved': True}).eq('id', comment_id).execute()
            elif action in ('reject', 'spam', 'purge'):
                response = supabase.table('comments').delete().eq('id', comment_id).execute()
            else:
                return jsonify({"error": f"Invalid action: {action}"}), 400
            return jsonify({"ok": True, "comment": response.data[0] if response.data else None})
        else:
            comments = load_local_comments()
            found = False
            new_comments = []
            target = None
            for c in comments:
                if str(c.get('id')) == str(comment_id):
                    found = True
                    if action == 'approve':
                        c['approved'] = True
                        new_comments.append(c)
                        target = c
                    elif action in ('reject', 'spam', 'purge'):
                        target = c
                    else:
                        return jsonify({"error": f"Invalid action: {action}"}), 400
                else:
                    new_comments.append(c)
            if not found:
                return jsonify({"error": "Comment not found"}), 404
            save_local_comments(new_comments)
            return jsonify({"ok": True, "comment": target})
    except Exception as e:
        print(f"Error moderating comment: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/stories/<id>', methods=['DELETE'])
def delete_story(id):
    try:
        token = request.headers.get('x-admin-token')
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected or token != expected:
            return jsonify({"error": "Forbidden"}), 403

        supabase = get_supabase_client()
        response = supabase.table('stories').delete().eq('id', id).execute()
        if not response.data:
            response = supabase.table('stories').delete().eq('slug', id).execute()
        
        return jsonify({"ok": True, "deleted": len(response.data) if response.data else 0})
    except Exception as e:
        print(f"Error deleting story: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/stories/<id>', methods=['PUT'])
def update_story(id):
    try:
        token = request.headers.get('x-admin-token')
        expected = os.environ.get('ADMIN_TOKEN')
        if not expected or token != expected:
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON body"}), 400

        supabase = get_supabase_client()
        
        current_resp = supabase.table('stories').select('*').eq('id', id).execute()
        if not current_resp.data:
            current_resp = supabase.table('stories').select('*').eq('slug', id).execute()
            if not current_resp.data:
                return jsonify({"error": "Story not found"}), 404
        
        story_to_update = current_resp.data[0]
        sid = story_to_update['id']

        update_fields = {}
        if 'headline' in data:
            update_fields['headline'] = data['headline']
        if 'body' in data:
            update_fields['body'] = data['body']
        if 'image_url' in data:
            update_fields['image_url'] = data['image_url']
        if 'excerpt' in data:
            update_fields['excerpt'] = data['excerpt']
        if 'tags' in data:
            if not isinstance(data['tags'], list):
                return jsonify({"error": "tags must be an array"}), 400
            parts = []
            for t in data['tags']:
                if isinstance(t, str):
                    parts.extend([p.strip() for p in t.split(',') if p.strip()])
            seen = set()
            deduped = []
            for t in parts:
                if t not in seen:
                    seen.add(t)
                    deduped.append(t)
            update_fields['tags'] = deduped
        if 'category' in data:
            update_fields['category'] = data['category']
        if 'status' in data:
            update_fields['status'] = data['status']
        if 'quality_score' in data:
            try:
                update_fields['quality_score'] = float(data['quality_score'])
            except (ValueError, TypeError):
                pass
        if 'slug' in data and data['slug']:
            update_fields['slug'] = data['slug']
        elif 'headline' in data:
            slug = data['headline'].lower()
            slug = re.sub(r'\s+', '-', slug)
            slug = re.sub(r'[^a-z0-9\-]', '', slug)
            update_fields['slug'] = slug

        response = supabase.table('stories').update(update_fields).eq('id', sid).execute()
        return jsonify(response.data[0]), 200
    except Exception as e:
        print(f"Error updating story: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port)