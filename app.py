import os
import json
import time
import random
import string
import re
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.')
CORS(app)

DATA_PATH = os.path.join(os.path.dirname(__file__), 'data.json')
stories = []

def load_from_disk():
    global stories
    try:
        if os.path.exists(DATA_PATH):
            with open(DATA_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    stories = data
    except Exception as e:
        print(f"Load failed: {e}")

def save_to_disk():
    # Vercel filesystem is read-only. This will only work locally.
    if os.environ.get('VERCEL'):
        return
    try:
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(stories, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Save failed: {e}")

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
        normalized_tags = [str(t).strip() for t in tags if str(t).strip()]

    if quality_score is not None:
        try:
            quality_score = float(quality_score)
        except (ValueError, TypeError):
            quality_score = None

    if not slug:
        slug = headline.lower()
        slug = re.sub(r'\s+', '-', slug)
        slug = re.sub(r'[^a-z0-9\-]', '', slug)

    now = datetime.utcnow().isoformat() + 'Z'
    
    return {
        "id": generate_id(),
        "created_at": now,
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

@app.route('/')
@app.route('/index.html')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"ok": True})

@app.route('/api/stories', methods=['GET'])
def get_stories():
    sorted_stories = sorted(stories, key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify(sorted_stories)

@app.route('/api/stories/<slug>', methods=['GET'])
def get_story(slug):
    story = next((s for s in stories if s.get('slug') == slug), None)
    if not story:
        return jsonify({"error": "Not found"}), 404
    return jsonify(story)

@app.route('/api/stories', methods=['POST'])
def create_story():
    try:
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
        stories.append(story)
        save_to_disk()
        return jsonify(story), 201
    except Exception as e:
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/stories', methods=['DELETE'])
def delete_stories():
    count = len(stories)
    stories.clear()
    save_to_disk()
    return jsonify({"ok": True, "deleted": count})

@app.route('/api/reset', methods=['POST', 'DELETE'])
def reset_stories():
    token = request.headers.get('x-admin-token')
    expected = os.environ.get('ADMIN_TOKEN')
    if not expected or token != expected:
        return jsonify({"error": "Forbidden"}), 403
    
    count = len(stories)
    stories.clear()
    save_to_disk()
    return jsonify({"ok": True, "deleted": count})

if __name__ == '__main__':
    load_from_disk()
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port)
else:
    load_from_disk()
