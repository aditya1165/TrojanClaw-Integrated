#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <supabase-project-ref>" >&2
  exit 2
fi

project_ref="$1"

if ! command -v npx >/dev/null 2>&1; then
  echo "npx is required. Install Node.js, then retry." >&2
  exit 1
fi

echo "Linking this repository to Supabase project: ${project_ref}"
npx supabase link --project-ref "${project_ref}"

echo "Previewing remote database migrations"
npx supabase db push --dry-run --remote

read -r -p "Apply the migrations shown above? [y/N] " confirmation
if [[ ! "${confirmation}" =~ ^[Yy]$ ]]; then
  echo "Migration cancelled. No remote schema changes were applied."
  exit 0
fi

npx supabase db push --remote

echo "Verifying the deployed schema"
npx supabase db query \
  "select to_regclass('public.documents') as documents, to_regclass('public.piazza_data') as piazza_data, exists (select 1 from pg_extension where extname = 'vector') as vector_enabled;" \
  --read-only --remote

echo "Supabase schema deployment complete."
