#!/bin/sh
# Writes the flower basic auth credentials used by the Caddyfile (not committed)
# Usage: ./flower_auth.sh <user> <password>
set -eu
cd "$(dirname "$0")"
hash=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$2")
echo "$1 $hash" > flower_auth
echo "Written $(pwd)/flower_auth"
