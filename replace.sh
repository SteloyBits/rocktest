#!/usr/bin/env bash
BASE_URL="https://rocktest-kzrz.vercel.app"
ADMIN_TOKEN="thebigsteloyrocks"
OLD_BROKEN_PREFIX="https://https://wgqzaavazpgpdthedfyi.supabase.co"
NEW_PREFIX="https://mruqtwewnzzpafllhktp.supabase.co"

# Fetch all stories, filter to broken ones, fix & PATCH
curl -s "$BASE_URL/api/stories" | \
jq -r --arg bad "$OLD_BROKEN_PREFIX" \
  '.[] | select(.image_url | startswith($bad)) | [.id, .image_url] | @tsv' | \
while IFS=$'\t' read -r id old_url; do
  # Strip the entire malformed prefix, re-attach the correct one
  path_suffix="${old_url#$OLD_BROKEN_PREFIX}"  # e.g. /storage/v1/object/...
  new_url="${NEW_PREFIX}${path_suffix}"

  echo "Fixing [$id]:"
  echo "  OLD: $old_url"
  echo "  NEW: $new_url"

  curl -s -X PUT "$BASE_URL/api/stories/$id" \
    -H "Content-Type: application/json" \
    -H "x-admin-token: $ADMIN_TOKEN" \
    -d "{\"image_url\": \"$new_url\"}" | jq '{id: .id, image_url: .image_url}'

  echo ""
done