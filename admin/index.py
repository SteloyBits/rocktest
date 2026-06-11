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
from supabase import create_client
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth

# Load environment variables from the parent directory's .env file
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
load_dotenv(dotenv_path)

# Initialize Firebase Admin
db = None
firebase_client_configured = False

try:
    firebase_project_id = os.environ.get('FIREBASE_PROJECT_ID')
    firebase_client_email = os.environ.get('FIREBASE_CLIENT_EMAIL')
    firebase_private_key = os.environ.get('FIREBASE_PRIVATE_KEY')

    if firebase_project_id and firebase_client_email and firebase_private_key:
        formatted_private_key = firebase_private_key.replace('\\n', '\n')
        
        cred = credentials.Certificate({
            "type": "service_account",
            "project_id": firebase_project_id,
            "private_key": formatted_private_key,
            "client_email": firebase_client_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        })
        
        firebase_admin.initialize_app(cred, name='admin_app')
        db = firestore.client(app=firebase_admin.get_app('admin_app'))
        firebase_client_configured = True
        print("Firebase Admin initialized successfully in Admin App.")
except Exception as e:
    print(f"Failed to initialize Firebase Admin in Admin App: {e}")

def get_supabase_client():
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in env")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def is_firebase_configured():
    return firebase_client_configured and db is not None

def generate_id():
    now_ms = int(time.time() * 1000)
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{now_ms}-{random_str}"

app = Flask(__name__, static_folder='.')
CORS(app)

# Helper to verify x-admin-token
def verify_admin_token():
    token = request.headers.get('x-admin-token')
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
            app = firebase_admin.get_app('admin_app') if 'admin_app' in firebase_admin._apps else None
            decoded_token = firebase_auth.verify_id_token(token, app=app)
            return True
        except Exception as e:
            print(f"Firebase token verification failed in admin server: {e}")
            return False

    return False

# Serve frontend files
@app.route('/')
@app.route('/login.html')
def serve_login():
    return send_from_directory('.', 'login.html')

@app.route('/index.html')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({
        "apiKey": os.environ.get('FIREBASE_API_KEY', ''),
        "authDomain": os.environ.get('FIREBASE_AUTH_DOMAIN', ''),
        "projectId": os.environ.get('FIREBASE_PROJECT_ID', ''),
        "storageBucket": os.environ.get('FIREBASE_STORAGE_BUCKET', ''),
        "messagingSenderId": os.environ.get('FIREBASE_MESSAGING_SENDER_ID', ''),
        "appId": os.environ.get('FIREBASE_APP_ID', ''),
        "measurementId": os.environ.get('FIREBASE_MEASUREMENT_ID', '')
    })

# Admin Dashboard Stats
@app.route('/api/admin/stats', methods=['GET'])
def get_admin_stats():
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403

    try:
        supabase = get_supabase_client()
        
        # 1. Fetch stories from Supabase
        stories_response = supabase.table('stories').select('quality_score, category').execute()
        stories = stories_response.data or []
        stories_count = len(stories)

        # Calculate average quality score
        quality_scores = [float(s['quality_score']) for s in stories if s.get('quality_score') is not None]
        avg_quality = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else 0.0

        # Calculate category distribution
        categories = {}
        for s in stories:
            cat = s.get('category') or 'General'
            categories[cat] = categories.get(cat, 0) + 1

        # 2. Fetch comments (Firestore or SQLite / local config fallback)
        comments_count = 0
        pending_count = 0

        if is_firebase_configured():
            docs = db.collection('comments').stream()
            for doc in docs:
                comments_count += 1
                d = doc.to_dict()
                if not d.get('approved', False):
                    pending_count += 1
        else:
            # Fallback to comments.json if Firebase is not active
            comments_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'comments.json')
            if os.path.exists(comments_file):
                try:
                    with open(comments_file, 'r', encoding='utf-8') as f:
                        comments = json.load(f)
                        comments_count = len(comments)
                        pending_count = len([c for c in comments if not c.get('approved', False)])
                except Exception:
                    pass

        return jsonify({
            "stories_count": stories_count,
            "comments_count": comments_count,
            "pending_comments_count": pending_count,
            "average_quality_score": avg_quality,
            "categories": categories
        })
    except Exception as e:
        print(f"Error fetching stats: {e}")
        return jsonify({"error": str(e)}), 500

# Stories CRUD
@app.route('/api/admin/stories', methods=['GET'])
def admin_get_stories():
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
        supabase = get_supabase_client()
        response = supabase.table('stories').select('*').order('created_at', desc=True).execute()
        return jsonify(response.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/stories', methods=['POST'])
def admin_create_story():
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
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
            "status": data.get('status', 'draft')
        }

        response = supabase.table('stories').insert(story).execute()
        return jsonify(response.data[0]), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/stories/<id>', methods=['PUT'])
def admin_update_story(id):
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
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
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/stories/<id>', methods=['DELETE'])
def admin_delete_story(id):
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
        supabase = get_supabase_client()
        response = supabase.table('stories').delete().eq('id', id).execute()
        if not response.data:
            response = supabase.table('stories').delete().eq('slug', id).execute()
        return jsonify({"ok": True, "deleted": len(response.data) if response.data else 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Comments Moderation
@app.route('/api/admin/comments', methods=['GET'])
def admin_get_comments():
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
        if is_firebase_configured():
            docs = db.collection('comments').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
            results = []
            for doc in docs:
                d = doc.to_dict()
                d['id'] = doc.id
                if 'created_at' in d and hasattr(d['created_at'], 'isoformat'):
                    d['created_at'] = d['created_at'].isoformat()
                elif 'created_at' in d:
                    d['created_at'] = str(d['created_at'])
                results.append(d)
            return jsonify(results)
        else:
            comments_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'comments.json')
            if os.path.exists(comments_file):
                with open(comments_file, 'r', encoding='utf-8') as f:
                    comments = json.load(f)
                    return jsonify(comments)
            return jsonify([])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/comments/moderate', methods=['POST'])
def admin_moderate_comment():
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
        data = request.get_json()
        if not data or 'id' not in data or 'action' not in data:
            return jsonify({"error": "Missing id or action"}), 400

        comment_id = data['id']
        action = data['action']

        if is_firebase_configured():
            doc_ref = db.collection('comments').document(comment_id)
            if action == 'approve':
                doc_ref.update({'approved': True})
                return jsonify({"ok": True})
            elif action in ('reject', 'spam', 'purge'):
                doc_ref.delete()
                return jsonify({"ok": True})
            return jsonify({"error": f"Invalid action: {action}"}), 400
        else:
            comments_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'comments.json')
            if os.path.exists(comments_file):
                with open(comments_file, 'r', encoding='utf-8') as f:
                    comments = json.load(f)
                new_comments = []
                for c in comments:
                    if str(c.get('id')) == str(comment_id):
                        if action == 'approve':
                            c['approved'] = True
                            new_comments.append(c)
                        elif action in ('reject', 'spam', 'purge'):
                            pass
                    else:
                        new_comments.append(c)
                with open(comments_file, 'w', encoding='utf-8') as f:
                    json.dump(new_comments, f, indent=2, ensure_ascii=False)
                return jsonify({"ok": True})
            return jsonify({"error": "No comment database found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Utilities
@app.route('/api/admin/normalize-tags', methods=['POST'])
def admin_normalize_tags():
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
        supabase = get_supabase_client()
        response = supabase.table('stories').select('id,tags').execute()
        rows = response.data or []
        updated = 0

        for r in rows:
            sid = r.get('id')
            tags_val = r.get('tags')
            parts = []
            if isinstance(tags_val, list):
                for t in tags_val:
                    if isinstance(t, str):
                        parts.extend([p.strip() for p in t.split(',') if p.strip()])
            elif isinstance(tags_val, str):
                parts = [p.strip() for p in tags_val.split(',') if p.strip()]

            seen = set()
            deduped = []
            for p in parts:
                if p not in seen:
                    seen.add(p)
                    deduped.append(p)

            if deduped != tags_val:
                supabase.table('stories').update({'tags': deduped}).eq('id', sid).execute()
                updated += 1

        return jsonify({"ok": True, "updated": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/reset', methods=['POST'])
def admin_reset_stories():
    if not verify_admin_token():
        return jsonify({"error": "Forbidden"}), 403
    try:
        supabase = get_supabase_client()
        response = supabase.table('stories').select('id').execute()
        count = len(response.data)
        if count > 0:
            ids = [s['id'] for s in response.data]
            supabase.table('stories').delete().in_('id', ids).execute()
        return jsonify({"ok": True, "deleted": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('ADMIN_PORT', 5001))
    print(f"Starting admin server on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port)
