# Supabase Comments Setup

1. Run the SQL in [comments.sql](comments.sql) in your Supabase SQL editor.
2. Set the backend environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
3. Redeploy or restart the Flask API so the new variables are picked up.
4. Open a story page and post a test comment.
5. Approve the row in Supabase if you want it to become visible publicly.

Notes:

- The frontend sends comments to `/api/comments`.
- Approved comments are returned by the GET endpoint and rendered in the story view.
- New comments default to `approved = false` for moderation.
