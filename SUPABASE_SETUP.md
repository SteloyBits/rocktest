# Supabase Comments Setup

1. For a fresh database, run [comments.sql](comments.sql). For an existing
   database, run [migrations/002_comment_moderation_status.sql](migrations/002_comment_moderation_status.sql).
2. Set the backend environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_KEY` (use the service-role key on the backend so protected admin
     endpoints can read and moderate non-public comments)
3. Redeploy or restart the Flask API so the new variables are picked up.
4. Open a story page and post a test comment.
5. Approve the row in Supabase if you want it to become visible publicly.

Notes:

- The frontend sends comments to `/api/comments`.
- Only comments with `status = 'APPROVED'` are returned publicly.
- New comments default to `status = 'PENDING_REVIEW'`.
- Never expose the service-role key to the browser.
