-- Add the canonical admin story model while preserving the legacy public columns.
alter table public.stories
  add column if not exists title text,
  add column if not exists content text,
  add column if not exists cover_image text,
  add column if not exists published_at timestamptz,
  add column if not exists updated_at timestamptz;

update public.stories
set
  title = coalesce(nullif(title, ''), headline),
  content = coalesce(nullif(content, ''), body),
  cover_image = coalesce(nullif(cover_image, ''), image_url),
  status = case when status = 'draft' then 'draft' else 'published' end,
  published_at = case
    when status = 'draft' then null
    else coalesce(published_at, created_at, now())
  end,
  updated_at = coalesce(updated_at, created_at, now());

alter table public.stories
  alter column title set not null,
  alter column content set not null,
  alter column cover_image set not null,
  alter column status set default 'draft',
  alter column status set not null,
  alter column updated_at set default now(),
  alter column updated_at set not null;

alter table public.stories
  drop constraint if exists stories_status_check;

alter table public.stories
  add constraint stories_status_check check (status in ('draft', 'published'));

create index if not exists stories_slug_idx
  on public.stories (slug);

create index if not exists stories_status_published_at_idx
  on public.stories (status, published_at desc);

create index if not exists stories_updated_at_idx
  on public.stories (updated_at desc);
