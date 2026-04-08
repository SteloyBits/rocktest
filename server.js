const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 3000;
const indexPath = path.join(__dirname, 'index.html');
const dataPath = path.join(__dirname, 'data.json');

const stories = [];

function loadFromDisk() {
  try {
    if (fs.existsSync(dataPath)) {
      const raw = fs.readFileSync(dataPath, 'utf8');
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) {
        stories.length = 0;
        for (const s of arr) stories.push(s);
      }
    }
  } catch {}
}

function saveToDisk() {
  try {
    // Vercel filesystem is read-only. This will only work locally.
    if (process.env.VERCEL) return; 
    fs.writeFileSync(dataPath, JSON.stringify(stories, null, 2), 'utf8');
  } catch (err) {
    console.error('Save failed:', err.message);
  }
}

function sendJSON(res, statusCode, data) {
  const body = JSON.stringify(data);
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function sendHTML(res, statusCode, html) {
  res.writeHead(statusCode, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(html);
}

function notFound(res) {
  sendJSON(res, 404, { error: 'Not found' });
}

function readRequestBody(req, maxBytes = 1_000_000) {
  return new Promise((resolve, reject) => {
    let chunks = [];
    let total = 0;
    req.on('data', (chunk) => {
      total += chunk.length;
      if (total > maxBytes) {
        reject(new Error('Payload too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      resolve(Buffer.concat(chunks).toString('utf8'));
    });
    req.on('error', (err) => reject(err));
  });
}

function normalizeStory(input) {
  const {
    headline = '',
    body = '',
    excerpt = '',
    image_url = '',
    tags = [],
    slug = '',
    meta_description = '',
    category = '',
    quality_score = null,
    status = '',
  } = input || {};

  let normalizedTags = [];
  if (Array.isArray(tags)) {
    normalizedTags = tags.map((t) => String(t).trim()).filter(Boolean);
  } else if (tags == null) {
    normalizedTags = [];
  }

  let quality = quality_score;
  if (quality != null) {
    const n = Number(quality);
    quality = Number.isFinite(n) ? n : null;
  }

  const now = new Date();
  return {
    id: `${now.getTime()}-${Math.random().toString(36).slice(2, 8)}`,
    created_at: now.toISOString(),
    headline: String(headline),
    body: String(body),
    excerpt: String(excerpt),
    image_url: String(image_url),
    tags: normalizedTags,
    slug: String(slug || String(headline).toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9\-]/g, '')),
    meta_description: String(meta_description),
    category: String(category),
    quality_score: quality,
    status: String(status),
  };
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const { pathname } = url;

  if (req.method === 'GET' && (pathname === '/' || pathname === '/index.html')) {
    fs.readFile(indexPath, 'utf8', (err, html) => {
      if (err) {
        sendHTML(res, 500, '<h1>500 Internal Server Error</h1>');
        return;
      }
      sendHTML(res, 200, html);
    });
    return;
  }

  if (req.method === 'GET' && pathname === '/api/health') {
    sendJSON(res, 200, { ok: true });
    return;
  }

  if (req.method === 'GET' && pathname === '/api/stories') {
    const sorted = [...stories].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    sendJSON(res, 200, sorted);
    return;
  }

  if (req.method === 'GET' && pathname.startsWith('/api/stories/')) {
    const slug = pathname.slice('/api/stories/'.length);
    const found = stories.find((s) => s.slug === slug);
    if (!found) {
      notFound(res);
      return;
    }
    sendJSON(res, 200, found);
    return;
  }

  if (req.method === 'POST' && pathname === '/api/stories') {
    try {
      const raw = await readRequestBody(req);
      let data;
      try {
        data = JSON.parse(raw);
      } catch {
        sendJSON(res, 400, { error: 'Invalid JSON body' });
        return;
      }
      const required = ['headline', 'body', 'image_url'];
      for (const key of required) {
        if (data[key] == null || data[key] === '') {
          sendJSON(res, 400, { error: `Missing required field: ${key}` });
          return;
        }
      }
      if (Object.prototype.hasOwnProperty.call(data, 'tags') && !Array.isArray(data.tags)) {
        sendJSON(res, 400, { error: 'tags must be an array' });
        return;
      }
      const story = normalizeStory(data);
      stories.push(story);
      saveToDisk();
      sendJSON(res, 201, story);
    } catch (err) {
      if (err && err.message === 'Payload too large') {
        sendJSON(res, 413, { error: 'Payload too large' });
        return;
      }
      sendJSON(res, 500, { error: 'Internal server error' });
    }
    return;
  }

  if (req.method === 'DELETE' && pathname === '/api/stories') {
    const deleted = stories.length;
    stories.length = 0;
    saveToDisk();
    sendJSON(res, 200, { ok: true, deleted });
    return;
  }

  if ((req.method === 'POST' || req.method === 'DELETE') && pathname === '/api/reset') {
    const token = req.headers['x-admin-token'];
    const expected = process.env.ADMIN_TOKEN;
    if (!expected || !token || token !== expected) {
      sendJSON(res, 403, { error: 'Forbidden' });
      return;
    }
    const deleted = stories.length;
    stories.length = 0;
    saveToDisk();
    sendJSON(res, 200, { ok: true, deleted });
    return;
  }

  notFound(res);
});

if (process.env.VERCEL) {
  module.exports = server;
} else {
  server.listen(PORT, () => {
    console.log(`Server running at http://localhost:${PORT}`);
  });
}

loadFromDisk();

