-- Normalize story lifecycle state and add public listing indexes.
alter table public.stories
  add column if not exists published_at timestamptz,
  add column if not exists updated_at timestamptz;

update public.stories
set
  status = case
    when status in ('draft', 'DRAFT') then 'DRAFT'
    else 'PUBLISHED'
  end,
  published_at = case
    when status in ('draft', 'DRAFT') then null
    else coalesce(published_at, created_at, now())
  end,
  updated_at = coalesce(updated_at, created_at, now());

alter table public.stories
  alter column status set default 'DRAFT',
  alter column status set not null,
  alter column updated_at set default now(),
  alter column updated_at set not null;

alter table public.stories
  drop constraint if exists stories_status_check;

alter table public.stories
  add constraint stories_status_check check (status in ('DRAFT', 'PUBLISHED'));

create index if not exists stories_status_idx
  on public.stories (status);

create index if not exists stories_published_at_idx
  on public.stories (published_at desc);

create index if not exists stories_created_at_idx
  on public.stories (created_at desc);

create index if not exists stories_status_published_at_idx
  on public.stories (status, published_at desc);
