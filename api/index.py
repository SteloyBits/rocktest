import os
import json
import time
import random
import string
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth

load_dotenv()

# Initialize Firebase Admin
db = None
firebase_client_configured = False

try:
    firebase_project_id = os.environ.get('FIREBASE_PROJECT_ID')
    firebase_client_email = os.environ.get('FIREBASE_CLIENT_EMAIL')
    firebase_private_key = os.environ.get('FIREBASE_PRIVATE_KEY')

    if firebase_project_id and firebase_client_email and firebase_private_key:
        # Format the private key to handle escaped newlines
        formatted_private_key = firebase_private_key.replace('\\n', '\n')
        
        cred = credentials.Certificate({
            "type": "service_account",
            "project_id": firebase_project_id,
            "private_key": formatted_private_key,
            "client_email": firebase_client_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        })
        
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        firebase_client_configured = True
        print("Firebase Admin initialized successfully with Firestore.")
except Exception as e:
    print(f"Failed to initialize Firebase Admin: {e}")

def is_firebase_configured():
    return firebase_client_configured and db is not None

def verify_admin_token(token):
    if not token:
        return False
        
    # 1. Check if token matches standard ADMIN_TOKEN env variable
    expected = os.environ.get('ADMIN_TOKEN')
    if expected and token == expected:
        return True

    # 2. Check if token is 'bypass' (only allowed if Firebase is NOT configured)
    if token == 'bypass':
        if not is_firebase_configured():
            return True
        else:
            return False

    # 3. If Firebase is configured, verify it as a Firebase ID Token
    if is_firebase_configured():
        try:
            decoded_token = firebase_auth.verify_id_token(token)
            return True
        except Exception as e:
            print(f"Firebase token verification failed in main API: {e}")
            return False

    return False

app = Flask(__name__, static_folder='..')
CORS(app)

# ---------------------------------------------------------------------------
# Dual-Supabase client helpers
#
# Image storage was migrated between two Supabase projects:
#   SUPABASE_URL / SUPABASE_KEY         → "legacy" project (stories/comments DB
#                                           + all images uploaded BEFORE migration)
#   NEW_SUPABASE_URL / NEW_SUPABASE_KEY → "primary" project (images uploaded
#                                           AFTER migration only)
#
# Use get_legacy_client() for all database access (stories, comments).
# Use get_primary_client() for storage ops on newly uploaded images.
# Which project owns an image is determined by the hostname embedded in the
# stored image_url — no extra DB column is needed.
# ---------------------------------------------------------------------------

def get_legacy_client():
    """Supabase client for the legacy project (stories DB + pre-migration images)."""
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY environment variables must be set")
    return create_client(url, key)

# Backward-compat alias — existing callers of get_supabase_client() keep working.
get_supabase_client = get_legacy_client

def get_primary_client():
    """Supabase client for the primary project (post-migration images)."""
    url = os.environ.get('NEW_SUPABASE_URL')
    key = os.environ.get('NEW_SUPABASE_KEY')
    if not url or not key:
        raise ValueError("NEW_SUPABASE_URL and NEW_SUPABASE_KEY environment variables must be set")
    return create_client(url, key)

# Backward-compat alias — existing callers of get_supabase_client_new() keep working.
get_supabase_client_new = get_primary_client

def _get_supabase_hosts():
    """Return the bare hostnames of both Supabase projects (legacy, primary)."""
    legacy_host = urlparse(os.environ.get('SUPABASE_URL', '')).hostname or ''
    primary_host = urlparse(os.environ.get('NEW_SUPABASE_URL', '')).hostname or ''
    return legacy_host, primary_host

def download_image_from_supabase(bucket_name, file_path, preferred='legacy'):
    """Download an image from Supabase storage with automatic cross-project fallback.

    Args:
        bucket_name: Storage bucket name.
        file_path: File path within the bucket.
        preferred: Which project to try first — 'legacy' or 'primary'.
                   Callers should pass 'primary' when the stored image_url
                   hostname matches the primary project, so we hit the right
                   endpoint on the first attempt instead of wasting a round-trip.
    """
    if preferred == 'primary':
        ordered = [('primary', get_primary_client), ('legacy', get_legacy_client)]
    else:
        ordered = [('legacy', get_legacy_client), ('primary', get_primary_client)]

    last_exc = None
    for label, client_fn in ordered:
        try:
            return client_fn().storage.from_(bucket_name).download(file_path)
        except Exception as exc:
            print(f"[image-router] Failed to download from {label} project: {exc}")
            last_exc = exc

    print(f"[image-router] Both Supabase endpoints failed for {bucket_name}/{file_path} — image may be orphaned.")
    assert last_exc is not None  # loop always sets last_exc before reaching here
    raise last_exc

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

def format_datetime(val):
    if val is None:
        return None
    if hasattr(val, 'isoformat'):
        return val.isoformat()
    if hasattr(val, 'to_datetime'):
        return val.to_datetime().isoformat()
    return str(val)


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

@app.route('/api/storage/<bucket_name>/<path:file_path>', methods=['GET'])
def get_storage_image(bucket_name, file_path):
    """Proxy endpoint for Supabase storage images with dual-project fallback.

    Query params:
        source: 'legacy' (default) or 'primary' — which Supabase project to try first.
                The frontend passes this based on the hostname in the stored image_url.
    """
    try:
        # Honour the caller's hint about which project owns this image so we
        # avoid an unnecessary round-trip to the wrong endpoint.
        source = request.args.get('source', 'legacy')
        image_data = download_image_from_supabase(bucket_name, file_path, preferred=source)
        ext = file_path.split('.')[-1].lower() if '.' in file_path else ''
        content_type_map = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp',
            'svg': 'image/svg+xml'
        }
        content_type = content_type_map.get(ext, 'application/octet-stream')
        return image_data, 200, {'Content-Type': content_type}
    except Exception as e:
        return jsonify({"error": str(e)}), 404

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"ok": True})

@app.route('/api/config', methods=['GET'])
def get_config():
    legacy_host, primary_host = _get_supabase_hosts()
    return jsonify({
        "apiKey": os.environ.get('FIREBASE_API_KEY', ''),
        "authDomain": os.environ.get('FIREBASE_AUTH_DOMAIN', ''),
        "projectId": os.environ.get('FIREBASE_PROJECT_ID', ''),
        "storageBucket": os.environ.get('FIREBASE_STORAGE_BUCKET', ''),
        "messagingSenderId": os.environ.get('FIREBASE_MESSAGING_SENDER_ID', ''),
        "appId": os.environ.get('FIREBASE_APP_ID', ''),
        "measurementId": os.environ.get('FIREBASE_MEASUREMENT_ID', ''),
        # The frontend uses these hostnames to route images to the correct Supabase
        # project and to implement onerror cross-project fallback in the browser.
        # legacy = pre-migration images (SUPABASE_URL host)
        # primary = post-migration images (NEW_SUPABASE_URL host)
        "supabaseStorageHosts": {
            "legacy": legacy_host,
            "primary": primary_host
        }
    })

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

        # Build initial query: order by created_at descending (LIFO)
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

            # apply ordering: LIFO (created_at descending)
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

        if is_firebase_configured():
            docs = db.collection('comments')\
                .where('path', '==', path)\
                .where('approved', '==', True)\
                .stream()
            results = []
            for doc in docs:
                d = doc.to_dict()
                d['id'] = doc.id
                if 'created_at' in d:
                    d['created_at'] = format_datetime(d['created_at'])
                results.append(d)
            # Sort in-memory to avoid requiring a composite index in Firestore
            try:
                results.sort(key=lambda x: x.get('created_at') or '')
            except Exception:
                pass
            return jsonify(results)
        elif is_supabase_configured():
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

        if is_firebase_configured():
            comment['created_at'] = datetime.now(timezone.utc)
            doc_ref = db.collection('comments').document()
            doc_ref.set(comment)
            inserted = comment.copy()
            inserted['id'] = doc_ref.id
            if hasattr(inserted['created_at'], 'isoformat'):
                inserted['created_at'] = inserted['created_at'].isoformat()
            return jsonify({
                "message": "Thanks. Your comment is awaiting moderation.",
                "comment": inserted,
            }), 201
        elif is_supabase_configured():
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
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403
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
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403
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
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403
        supabase = get_supabase_client()
        
        response = supabase.table('stories').select('id').execute()
        count = len(response.data)
        if count > 0:
            ids = [s['id'] for s in response.data]
            supabase.table('stories').delete().in_('id', ids).execute()
        return jsonify({"ok": True, "deleted": count})
    except Exception as e:
        print(f"Error resetting stories: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/stats', methods=['GET'])
def admin_get_stats():
    try:
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403

        supabase = get_supabase_client()

        # Fetch stories for aggregation
        stories_response = supabase.table('stories').select('quality_score, category').execute()
        stories = stories_response.data or []
        stories_count = len(stories)

        quality_scores = [float(s['quality_score']) for s in stories if s.get('quality_score') is not None]
        avg_quality = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else 0.0

        categories = {}
        for s in stories:
            cat = s.get('category') or 'General'
            categories[cat] = categories.get(cat, 0) + 1

        comments_count = 0
        pending_count = 0

        if is_firebase_configured():
            docs = db.collection('comments').stream()
            for doc in docs:
                comments_count += 1
                d = doc.to_dict()
                if not d.get('approved', False):
                    pending_count += 1
        elif is_supabase_configured():
            resp_all = supabase.table('comments').select('approved').execute()
            for c in (resp_all.data or []):
                comments_count += 1
                if not c.get('approved', False):
                    pending_count += 1
        else:
            comments = load_local_comments()
            comments_count = len(comments)
            pending_count = len([c for c in comments if not c.get('approved', False)])

        return jsonify({
            "stories_count": stories_count,
            "comments_count": comments_count,
            "pending_comments_count": pending_count,
            "average_quality_score": avg_quality,
            "categories": categories
        })
    except Exception as e:
        print(f"Error fetching admin stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/stories', methods=['GET'])
def admin_get_stories():
    try:
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403

        supabase = get_supabase_client()
        response = supabase.table('stories').select('*').order('created_at', desc=True).execute()
        return jsonify(response.data or [])
    except Exception as e:
        print(f"Error fetching admin stories: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/stories', methods=['POST'])
def admin_create_story():
    try:
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403

        supabase = get_supabase_client()
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        headline = data.get('headline')
        body = data.get('body')
        image_url = data.get('image_url')

        if not headline or not body or not image_url:
            return jsonify({"error": "Missing headline, body or image_url"}), 400

        slug = data.get('slug')
        if not slug:
            slug = headline.lower()
            slug = re.sub(r'\s+', '-', slug)
            slug = re.sub(r'[^a-z0-9\-]', '', slug)

        story = {
            "id": generate_id(),
            "headline": headline,
            "body": body,
            "excerpt": data.get('excerpt', ''),
            "image_url": image_url,
            "tags": data.get('tags', []),
            "slug": slug,
            "category": data.get('category', 'General'),
            "quality_score": float(data['quality_score']) if data.get('quality_score') is not None else None,
            "status": data.get('status', 'draft'),
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        response = supabase.table('stories').insert(story).execute()
        return jsonify(response.data[0]), 201
    except Exception as e:
        print(f"Error creating admin story: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/stories/<id>', methods=['PUT'])
def admin_update_story(id):
    try:
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403

        supabase = get_supabase_client()
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        update_fields = {}
        fields = ['headline', 'body', 'excerpt', 'image_url', 'category', 'tags', 'status', 'slug']
        for f in fields:
            if f in data:
                update_fields[f] = data[f]

        if 'quality_score' in data:
            update_fields['quality_score'] = float(data['quality_score']) if data['quality_score'] is not None else None

        response = supabase.table('stories').update(update_fields).eq('id', id).execute()
        if not response.data:
            response = supabase.table('stories').update(update_fields).eq('slug', id).execute()

        return jsonify(response.data[0] if response.data else {"ok": True})
    except Exception as e:
        print(f"Error updating admin story: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/stories/<id>', methods=['DELETE'])
def admin_delete_story(id):
    try:
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403

        supabase = get_supabase_client()
        response = supabase.table('stories').delete().eq('id', id).execute()
        if not response.data:
            response = supabase.table('stories').delete().eq('slug', id).execute()
        return jsonify({"ok": True, "deleted": len(response.data) if response.data else 0})
    except Exception as e:
        print(f"Error deleting admin story: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/normalize-tags', methods=['POST'])
def admin_normalize_tags():
    try:
        token = request.headers.get('x-admin-token')
        if not verify_admin_token(token):
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
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403

        if is_firebase_configured():
            docs = db.collection('comments')\
                .order_by('created_at', direction=firestore.Query.DESCENDING)\
                .stream()
            results = []
            for doc in docs:
                d = doc.to_dict()
                d['id'] = doc.id
                if 'created_at' in d:
                    d['created_at'] = format_datetime(d['created_at'])
                results.append(d)
            return jsonify(results)
        elif is_supabase_configured():
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
        if not verify_admin_token(token):
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json()
        if not data or 'id' not in data or 'action' not in data:
            return jsonify({"error": "Missing id or action"}), 400

        comment_id = data['id']
        action = data['action'] # 'approve', 'reject', 'spam', 'purge'

        if is_firebase_configured():
            doc_ref = db.collection('comments').document(comment_id)
            if action == 'approve':
                doc_ref.update({'approved': True})
                updated_doc = doc_ref.get()
                comment_data = updated_doc.to_dict() if updated_doc.exists else None
                if comment_data:
                    comment_data['id'] = doc_ref.id
                    if 'created_at' in comment_data:
                        comment_data['created_at'] = format_datetime(comment_data['created_at'])
                return jsonify({"ok": True, "comment": comment_data})
            elif action in ('reject', 'spam', 'purge'):
                doc_ref.delete()
                return jsonify({"ok": True, "comment": {"id": comment_id}})
            else:
                return jsonify({"error": f"Invalid action: {action}"}), 400
        elif is_supabase_configured():
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
        if not verify_admin_token(token):
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
        if not verify_admin_token(token):
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
    app.run(host='127.0.0.1', port=port)