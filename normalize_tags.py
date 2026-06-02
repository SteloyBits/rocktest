"""Normalize tags for stories in Supabase.

Usage:
  Set SUPABASE_URL and SUPABASE_KEY in your environment, then:
    python scripts/normalize_tags.py

This script will:
 - fetch all stories
 - normalize tags by splitting comma-separated tag strings, trimming whitespace
 - de-duplicate tags while preserving order
 - update each story row with the normalized tags

Be careful: run in a safe environment and/or test on a copy of your DB first.
"""
import os
import sys
from supabase import create_client


def get_client():
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not url or not key:
        print('SUPABASE_URL and SUPABASE_KEY must be set')
        sys.exit(1)
    return create_client(url, key)


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

    # dedupe preserving order
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main():
    supabase = get_client()
    resp = supabase.table('stories').select('id,tags').execute()
    rows = resp.data or []
    print(f'Fetched {len(rows)} stories')
    updates = 0
    for r in rows:
        sid = r.get('id')
        tags_val = r.get('tags')
        normalized = normalize_tags_list(tags_val)
        if normalized != tags_val:
            supabase.table('stories').update({'tags': normalized}).eq('id', sid).execute()
            updates += 1
    print(f'Updated {updates} rows')


if __name__ == '__main__':
    main()
