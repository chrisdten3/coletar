#!/usr/bin/env bash
# Run the workspace locally against the *hosted* database and real Supabase Auth.
#
# This is the demo rehearsal: same database, same identity provider, same sign-in
# the audience will see, but on localhost where a mistake costs nothing. It is the
# only way to find out that a deployment setting is wrong before the deployment is
# the thing in front of people.
#
# Secrets are read from .env at start-up rather than written into
# .claude/launch.json, which is committed. The anon key is publishable and would be
# harmless there; the database password beside it would not, and a file that holds
# one of them eventually holds the other.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env — this needs COLETAR_DEPLOY_DATABASE_URL and the Supabase keys." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

for required in COLETAR_DEPLOY_DATABASE_URL COLETAR_SUPABASE_URL COLETAR_SUPABASE_ANON_KEY; do
  if [ -z "${!required:-}" ]; then
    echo "$required is not set in .env" >&2
    exit 1
  fi
done

# Point the store at the hosted database. Deliberately the deploy DSN rather than
# COLETAR_DATABASE_URL, which in .env is the laptop's Postgres.
export COLETAR_STORE_BACKEND=postgres
export COLETAR_DATABASE_URL="$COLETAR_DEPLOY_DATABASE_URL"
export COLETAR_IDENTITY_PROVIDER=supabase
export COLETAR_EMBEDDING_BACKEND=hashing
export COLETAR_EMBEDDING_DIM=768
export COLETAR_OPEN_REGISTRATION=true

# Left unset on purpose. Setting it would mark this hosted, and a hosted
# deployment must not be served over plain loopback with no TLS in front of it.
unset COLETAR_PUBLIC_URL || true

exec .venv/bin/python -m uvicorn coletar.inspector.app:app \
  --host 127.0.0.1 --port 8792
