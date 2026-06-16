# Rocktest API Documentation (Frontend Integration Guide)

## Admin Story API Contract

The admin dashboard should use `/api/admin/stories` and send the configured
`x-admin-token` header. Admin story responses use `title`, `content`,
`coverImage`, `tags`, `status`, `publishedAt`, `createdAt`, and `updatedAt`.
Story status values are `DRAFT` and `PUBLISHED`; legacy lowercase inputs are
accepted by the backend for compatibility.

- `GET /api/admin/stories` lists drafts and published stories.
- `POST /api/admin/stories` creates a draft.
- `PUT /api/admin/stories/<id>` updates title, content, cover image, tags,
  excerpt, meta description, category, quality score, slug, and optionally
  status. Legacy payload keys (`headline`, `body`, `image_url`) are accepted.
- `PATCH /api/admin/stories/<id>/publish` publishes a story.
- `PATCH /api/admin/stories/<id>/draft` returns a story to draft.
- `DELETE /api/admin/stories/<id>` permanently deletes a story.

Public `GET /api/stories` and `GET /api/stories/<slug>` only expose stories
whose status is `PUBLISHED`. Public lists sort by newest `publishedAt` first,
falling back to `createdAt` for migrated legacy stories. Public responses retain
the legacy `headline`, `body`, and `image_url` aliases used by the current
static frontend.

## Comment Moderation API Contract

Public comment submission uses `POST /api/comments` with `story_id`,
`author_name`, `author_email`, and `content`. New comments always return
`status: PENDING_REVIEW`. Use `GET /api/stories/<story_id>/comments` to load
approved comments; no other status is exposed publicly.

Authenticated admin requests use the configured `x-admin-token` header:

- `GET /api/admin/comments?status=PENDING_REVIEW` lists and filters comments.
- `PATCH /api/admin/comments/<id>/approve`
- `PATCH /api/admin/comments/<id>/reject`
- `PATCH /api/admin/comments/<id>/spam`
- `PATCH /api/admin/comments/<id>/pending`
- `DELETE /api/admin/comments/<id>`

The legacy `GET /api/comments?path=story:<slug>` and
`POST /api/admin/comments/moderate` interfaces remain available for existing
clients.

This document provides a comprehensive guide to the backend API endpoints available for the **Media Site Frontend** and the **Admin Media Dashboard**.

The backend is built with **Flask** and configured to run on **Vercel Serverless Functions**. It dynamically switches between **Supabase Database Mode** and **Local Fallback Mode** (using local JSON files) depending on the environment configuration.

---

## Table of Contents
1. [Base URL & Deployment](#base-url--deployment)
2. [Database Modes & Configurations](#database-modes--configurations)
3. [Authentication](#authentication)
4. [Data Schemas](#data-schemas)
5. [Public Endpoints](#public-endpoints)
    - [Get Health Status (`GET /api/health`)](#get-health-status-get-apihealth)
    - [List Stories (`GET /api/stories`)](#list-stories-get-apistories)
    - [Get Single Story (`GET /api/stories/<slug>`)](#get-single-story-get-apistoriesslug)
    - [List Approved Comments (`GET /api/comments`)](#list-approved-comments-get-apicomments)
    - [Post Comment (`POST /api/comments`)](#post-comment-post-apicomments)
6. [Administrative Endpoints](#administrative-endpoints)
    - [Create Story (`POST /api/stories`)](#create-story-post-apistories)
    - [Update Story (`PUT /api/stories/<id>`)](#update-story-put-apistoriesid)
    - [Delete Story (`DELETE /api/stories/<id>`)](#delete-story-delete-apistoriesid)
    - [List All Comments (`GET /api/admin/comments`)](#list-all-comments-get-apiadmincomments)
    - [Moderate Comment (`POST /api/admin/comments/moderate`)](#moderate-comment-post-apiadmincommentsmoderate)
    - [Normalize All Tags (`POST /api/admin/normalize-tags`)](#normalize-all-tags-post-apiadminnormalize-tags)
    - [Reset/Purge All Stories (`POST` or `DELETE /api/reset`)](#resetpurge-all-stories-post-or-delete-apireset)
7. [Frontend Integration Examples (React / Fetch)](#frontend-integration-examples-react--fetch)

---

## Base URL & Deployment

*   **Local Development**: `http://localhost:3000` (or configured `PORT` env variable)
*   **Production Vercel URL**: `https://rocktest-kzrz.vercel.app/`
*   **Routing Rules**:
    *   `/api/*` requests route to the backend Flask application in `api/index.py`.
    *   Static routes route to the single-page application `index.html`.

---

## Database Modes & Configurations

The API automatically detects the available database engine:
1.  **Supabase Mode**: Triggered when both `SUPABASE_URL` and `SUPABASE_KEY` are present in the environment variables. Read/Write actions are performed directly on Supabase PostgreSQL tables.
2.  **Local Fallback Mode**: Triggered if environment variables are missing. Data is loaded from and persisted to local JSON files (`data.json` for stories, `comments.json` for comments) inside the project directory.

> [!NOTE]
> Database modes affect comment auto-approval behaviors (see [Post Comment](#post-comment-post-apicomments) details).

---

## Authentication

All administrative endpoints require authentication via a secret token.

*   **Header Name**: `x-admin-token`
*   **Required Value**: Matches the `ADMIN_TOKEN` environment variable on the server.
*   **Default Dev Token**: `thebigsteloyrocks`

> [!WARNING]
> Administrative requests made without a valid `x-admin-token` header will return an HTTP `403 Forbidden` response:
> ```json
> {
>   "error": "Forbidden"
> }
> ```

---

## Data Schemas

### 1. Story Object
```json
{
  "id": "1686256983000-abcd12",
  "headline": "Example Story Headline",
  "body": "# Markdown Content\nThis is the markdown body of the story...",
  "excerpt": "A short teaser description...",
  "image_url": "https://example.com/images/hero.jpg",
  "tags": ["politics", "opinion"],
  "slug": "example-story-headline",
  "meta_description": "Meta description for search engines...",
  "category": "opinion",
  "quality_score": 92.5,
  "status": "published",
  "created_at": "2026-06-08T18:00:00.000Z"
}
```

### 2. Comment Object
```json
{
  "id": 12,
  "path": "story:example-story-headline",
  "author": "Jane Doe",
  "email": "jane@example.com",
  "url": "https://janedoe.com",
  "text": "This is a wonderful write-up!",
  "approved": false,
  "created_at": "2026-06-08T18:05:00.000Z"
}
```

---

## Public Endpoints

### Get Health Status
Verify that the server and API route is active and healthy.

*   **URL**: `/api/health`
*   **Method**: `GET`
*   **Auth Required**: No
*   **Response (200 OK)**:
    ```json
    {
      "ok": true
    }
    ```

---

### List Stories
Retrieve a list of news stories. Stories are returned sorted by publication date (`created_at` descending) by default.

*   **URL**: `/api/stories`
*   **Method**: `GET`
*   **Auth Required**: No
*   **Query Parameters**:
    *   `top_n` *(optional, integer)*: Maximum number of stories to return.
    *   `filters` *(optional, string)*: Comma-separated query strings. Can filter stories by **category** or **tags**.
    *   *Special Filter*: Include `popular` in the `filters` string to sort results by `quality_score` (descending) instead of `created_at`.
*   **Response (200 OK)**:
    ```json
    [
      {
        "id": "1686256983000-abcd12",
        "headline": "Tech Giants Agree on Standards",
        "body": "Story body text...",
        "excerpt": "Brief excerpt...",
        "image_url": "https://example.com/image.jpg",
        "tags": ["tech", "standards"],
        "slug": "tech-giants-standards",
        "meta_description": "SEO description",
        "category": "technology",
        "quality_score": 85.0,
        "status": "published",
        "created_at": "2026-06-08T18:00:00Z"
      }
    ]
    ```

---

### Get Single Story
Fetch details for a single news story by its slug identifier.

*   **URL**: `/api/stories/<slug>`
*   **Method**: `GET`
*   **Auth Required**: No
*   **Path Parameters**:
    *   `slug` *(string, required)*: The URL slug of the story (e.g. `tech-giants-standards`). Falls back to matching by `id` in local JSON mode.
*   **Response (200 OK)**: Story Object.
*   **Response (404 Not Found)**:
    ```json
    {
      "error": "Not found"
    }
    ```

---

### List Approved Comments
Retrieve all approved comments associated with a specific path/identifier.

*   **URL**: `/api/comments`
*   **Method**: `GET`
*   **Auth Required**: No
*   **Query Parameters**:
    *   `path` *(string, required)*: Identifier representing the story or page. High-level path structure should follow `story:<slug>` (e.g. `story:tech-giants-standards`).
*   **Response (200 OK)**: Array of comment objects sorted by `created_at` (ascending).
    ```json
    [
      {
        "id": 1,
        "path": "story:tech-giants-standards",
        "author": "Alice",
        "url": "",
        "text": "Great insights!",
        "created_at": "2026-06-08T18:05:00Z"
      }
    ]
    ```
*   **Response (400 Bad Request)**:
    ```json
    {
      "error": "Missing required query param: path"
    }
    ```

---

### Post Comment
Submit a comment to a story.

*   **URL**: `/api/comments`
*   **Method**: `POST`
*   **Auth Required**: No
*   **Request Body (JSON)**:
    ```json
    {
      "path": "story:tech-giants-standards",
      "text": "This is my comment body.",
      "author": "Bob Smith",
      "email": "bob@example.com",
      "url": "https://bobsmith.me"
    }
    ```
    *   `path` *(string, required)*: Must be 200 characters or fewer.
    *   `text` *(string, required)*: Must be 4000 characters or fewer.
    *   `author` *(string, optional)*: Must be 120 characters or fewer.
    *   `email` *(string, optional)*: Must be 200 characters or fewer.
    *   `url` *(string, optional)*: Must be 500 characters or fewer.
*   **Moderation Logic**:
    *   **Supabase Mode**: The comment is created with `approved = false`. It remains hidden from public `GET /api/comments` results until an admin approves it.
    *   **Local Mode**: The comment is automatically saved as `approved = true` (auto-approved).
*   **Response (210 Created)**:
    ```json
    {
      "message": "Thanks. Your comment is awaiting moderation.",
      "comment": {
        "id": 2,
        "path": "story:tech-giants-standards",
        "author": "Bob Smith",
        "email": "bob@example.com",
        "url": "https://bobsmith.me",
        "text": "This is my comment body.",
        "approved": false,
        "created_at": "2026-06-08T18:10:00Z"
      }
    }
    ```
*   **Response (400 Bad Request)**: Returned if payload constraints are violated or fields are missing.
    ```json
    {
      "error": "Missing required field: text"
    }
    ```

---

## Administrative Endpoints

> [!IMPORTANT]
> All admin endpoints (except `POST /api/stories` and `DELETE /api/stories` due to minor routing/omission differences in the underlying Python Flask app) enforce authentication. Ensure you include the `x-admin-token` header for consistency.

### Create Story
Create a new news story.

*   **URL**: `/api/stories`
*   **Method**: `POST`
*   **Auth Required**: None * (Can be requested without `x-admin-token` at HTTP layer, but intended for admin use).
*   **Request Body (JSON)**:
    ```json
    {
      "headline": "New Story Headline",
      "body": "Markdown or text contents...",
      "image_url": "https://example.com/image.jpg",
      "excerpt": "Teaser text...",
      "tags": ["opinion", "tech"],
      "category": "opinion",
      "slug": "custom-slug-value",
      "quality_score": 90.0,
      "status": "draft"
    }
    ```
    *   `headline` *(string, required)*
    *   `body` *(string, required)*
    *   `image_url` *(string, required)*
    *   `tags` *(array of strings, optional)*
    *   `slug` *(string, optional)*: Auto-generated from `headline` if omitted.
    *   `status` *(string, optional)*: Usually `'draft'`, `'review'`, or `'published'`.
*   **Response (201 Created)**: Story Object.
*   **Response (400 Bad Request)**:
    ```json
    {
      "error": "Missing required field: headline"
    }
    ```

---

### Update Story
Update an existing story.

*   **URL**: `/api/stories/<id>`
*   **Method**: `PUT`
*   **Auth Required**: Yes (`x-admin-token` header required)
*   **Path Parameters**:
    *   `id` *(string, required)*: The unique ID or slug of the story.
*   **Request Body (JSON)**:
    Any subset of the story schema to update (e.g. `headline`, `body`, `image_url`, `excerpt`, `tags`, `category`, `status`, `quality_score`, `slug`).
*   **Response (200 OK)**: Updated Story Object.
*   **Response (404 Not Found)**:
    ```json
    {
      "error": "Story not found"
    }
    ```

---

### Delete Story
Remove a story from the system.

*   **URL**: `/api/stories/<id>`
*   **Method**: `DELETE`
*   **Auth Required**: Yes (`x-admin-token` header required)
*   **Path Parameters**:
    *   `id` *(string, required)*: The unique ID or slug of the story.
*   **Response (200 OK)**:
    ```json
    {
      "ok": true,
      "deleted": 1
    }
    ```

---

### List All Comments
Retrieve all comments in the system, including pending and approved comments. This is used on the Admin Moderation Dashboard.

*   **URL**: `/api/admin/comments`
*   **Method**: `GET`
*   **Auth Required**: Yes (`x-admin-token` header required)
*   **Response (200 OK)**: Array of all comment objects, sorted by `created_at` (descending).
    ```json
    [
      {
        "id": 1,
        "path": "story:tech-giants-standards",
        "author": "Alice",
        "email": "alice@example.com",
        "url": "",
        "text": "Great insights!",
        "approved": false,
        "created_at": "2026-06-08T18:05:00Z"
      }
    ]
    ```

---

### Moderate Comment
Approve or reject a comment waiting in moderation.

*   **URL**: `/api/admin/comments/moderate`
*   **Method**: `POST`
*   **Auth Required**: Yes (`x-admin-token` header required)
*   **Request Body (JSON)**:
    ```json
    {
      "id": 1,
      "action": "approve"
    }
    ```
    *   `id` *(number/string, required)*: The database ID of the comment.
    *   `action` *(string, required)*: Must be one of the following:
        *   `"approve"`: Sets `approved = true`. The comment will now be visible on public endpoints.
        *   `"reject"`, `"spam"`, `"purge"`: Permanently deletes the comment from the database.
*   **Response (200 OK)**:
    ```json
    {
      "ok": true,
      "comment": {
        "id": 1,
        "approved": true,
        ...
      }
    }
    ```
*   **Response (400 Bad Request)**:
    ```json
    {
      "error": "Missing id or action"
    }
    ```
*   **Response (404 Not Found)**:
    ```json
    {
      "error": "Comment not found"
    }
    ```

---

### Normalize All Tags
Utility endpoint to scan all stories and clean up tags (unifying comma-separated string arrays, stripping whitespaces, and deduplicating).

*   **URL**: `/api/admin/normalize-tags`
*   **Method**: `POST`
*   **Auth Required**: Yes (`x-admin-token` header required)
*   **Response (200 OK)**:
    ```json
    {
      "ok": true,
      "updated": 3
    }
    ```

---

### Reset/Purge All Stories

> [!CAUTION]
> These endpoints will permanently delete ALL stories in the database. Use with extreme caution.

*   **Endpoint Options**:
    1.  `DELETE /api/stories`
        *   **Auth Required**: None (Warning: The standard endpoint does not enforce authentication at the HTTP level.)
    2.  `POST /api/reset` or `DELETE /api/reset`
        *   **Auth Required**: Yes (`x-admin-token` header required)
*   **Response (200 OK)**:
    ```json
    {
      "ok": true,
      "deleted": 14
    }
    ```

---

## Frontend Integration Examples (React / Fetch)

### 1. Fetching Public Stories in React
```javascript
import { useEffect, useState } from 'react';

export function StoryList() {
  const [stories, setStories] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/stories?filters=popular&top_n=10')
      .then((res) => {
        if (!res.ok) throw new Error('Failed to load stories');
        return res.json();
      })
      .then((data) => setStories(data))
      .catch((err) => setError(err.message));
  }, []);

  if (error) return <div>Error: {error}</div>;

  return (
    <div>
      {stories.map((story) => (
        <article key={story.id}>
          <h2>{story.headline}</h2>
          <p>{story.excerpt}</p>
        </article>
      ))}
    </div>
  );
}
```

### 2. Moderating a Comment on the Admin Dashboard
```javascript
async function approveComment(commentId) {
  const ADMIN_TOKEN = 'thebigsteloyrocks'; // Keep secure

  try {
    const response = await fetch('/api/admin/comments/moderate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-admin-token': ADMIN_TOKEN
      },
      body: JSON.stringify({
        id: commentId,
        action: 'approve'
      })
    });

    if (!response.ok) {
      const errData = await response.json();
      throw new Error(errData.error || 'Failed to moderate comment');
    }

    const result = await response.json();
    console.log('Comment approved:', result.comment);
  } catch (error) {
    console.error('Moderation error:', error.message);
  }
}
```
