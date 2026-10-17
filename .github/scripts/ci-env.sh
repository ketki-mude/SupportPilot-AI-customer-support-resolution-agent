#!/usr/bin/env sh
# Write .env for CI from .env.example, filling the required secrets with random throwaway values.
# No model API key is set, so nothing in CI can call a paid model.
set -eu

cp .env.example .env
for name in POSTGRES_PASSWORD JWT_SECRET SEED_CUSTOMER_PASSWORD SEED_AGENT_PASSWORD \
  SEED_SUPERVISOR_PASSWORD SEED_ADMIN_PASSWORD; do
  value=$(openssl rand -hex 24)
  awk -v key="$name" -v value="$value" '$0 == key "=" { print key "=" value; next } { print }' \
    .env > .env.tmp
  mv .env.tmp .env
done
if grep -Eq '^(GOOGLE|ANTHROPIC|OPENAI)_API_KEY=.+' .env; then
  echo "CI must not configure a model API key" >&2
  exit 1
fi
