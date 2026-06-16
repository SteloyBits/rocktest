-- Migrate the existing approved/path comment model to explicit moderation statuses.
alter table public.comments
  add column if not exists story_id text,
  add column if not exists author_name text,
  add column if not exists author_email text,
  add column if not exists content text,
  add column if not exists status text,
  add column if not exists updated_at timestamptz;

update public.comments
set
  story_id = coalesce(
    nullif(story_id, ''),
    nullif(regexp_replace(path, '^story:', ''), '')
  ),
  author_name = coalesce(nullif(author_name, ''), nullif(author, ''), 'Unknown'),
  author_email = coalesce(nullif(author_email, ''), nullif(email, ''), 'unknown@example.invalid'),
  content = coalesce(nullif(content, ''), text),
  status = coalesce(
    nullif(status, ''),
    case when approved then 'APPROVED' else 'PENDING_REVIEW' end
  ),
  updated_at = coalesce(updated_at, created_at, now());

-- Convert legacy slug-based associations to immutable story IDs where possible.
update public.comments as comments
set story_id = stories.id::text
from public.stories as stories
where comments.story_id = stories.slug;

alter table public.comments
  alter column story_id set not null,
  alter column author_name set not null,
  alter column author_email set not null,
  alter column content set not null,
  alter column status set default 'PENDING_REVIEW',
  alter column status set not null,
  alter column updated_at set default now(),
  alter column updated_at set not null;

alter table public.comments
  drop constraint if exists comments_status_check;

alter table public.comments
  add constraint comments_status_check
    check (status in ('PENDING_REVIEW', 'APPROVED', 'REJECTED', 'SPAM'));

drop policy if exists "public_select_approved" on public.comments;
create policy "public_select_approved" on public.comments
  for select using (status = 'APPROVED');

drop policy if exists "public_insert" on public.comments;
create policy "public_insert" on public.comments
  for insert with check (status = 'PENDING_REVIEW');

create index if not exists comments_status_idx on public.comments (status);
create index if not exists comments_story_id_idx on public.comments (story_id);
create index if not exists comments_created_at_idx on public.comments (created_at desc);
create index if not exists comments_story_status_created_idx
  on public.comments (story_id, status, created_at);
