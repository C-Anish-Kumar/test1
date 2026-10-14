#!/usr/bin/env sh
# Creates .env from .env.example and fills in generated secrets (idempotent).
set -eu
cd "$(dirname "$0")"

[ -f .env ] || cp .env.example .env

gen() { openssl rand -hex 32; }

fill() {
  if grep -qE "^$1=.+" .env; then
    echo "kept      $1"
  else
    value=$(gen)
    if grep -qE "^$1=" .env; then
      sed -i.bak "s|^$1=.*|$1=$value|" .env && rm -f .env.bak
    else
      echo "$1=$value" >> .env
    fi
    echo "generated $1"
  fi
}

for key in BETTER_AUTH_SECRET MONGO_ROOT_PASSWORD INNGEST_EVENT_KEY INNGEST_SIGNING_KEY; do
  fill "$key"
done
chmod 600 .env

if ! grep -qE '^NEXT_PUBLIC_FINNHUB_API_KEY=.+' .env; then
  echo
  echo "Next: add your Finnhub key (free at https://finnhub.io/register) to .env:"
  echo "  NEXT_PUBLIC_FINNHUB_API_KEY=..."
fi
echo
echo "Then start everything with: docker compose up -d --build"
