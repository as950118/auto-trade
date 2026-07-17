#!/usr/bin/env bash
# auto-trade-view.duckdns.org 전용 — 백엔드 + nginx + Let's Encrypt
#
# 사전:
#   1) DuckDNS에서 auto-trade-view → 이 서버 공인 IP 등록
#   2) 코드가 APP_DIR에 있음 (기본 /opt/auto-trade)
#   3) 방화벽 80, 443 개방
#
# 사용:
#   sudo bash deploy/setup-duckdns.sh you@email.com
#   sudo APP_DIR=/home/ubuntu/auto-trade bash deploy/setup-duckdns.sh you@email.com

set -euo pipefail

DOMAIN="auto-trade-view.duckdns.org"
EMAIL="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -z "$EMAIL" ]]; then
  echo "Usage: sudo bash deploy/setup-duckdns.sh <EMAIL>"
  echo "Example: sudo bash deploy/setup-duckdns.sh admin@gmail.com"
  exit 1
fi

echo "DOMAIN=$DOMAIN"
exec bash "$SCRIPT_DIR/setup.sh" "$DOMAIN" "$EMAIL"
