#!/usr/bin/env bash
# auto-trade-view.duckdns.org — Let's Encrypt 발급/갱신만
# (nginx·gunicorn 이미 설치된 뒤, SSL만 다시 할 때)
#
# 사전: DuckDNS A레코드 = 서버 IP, 80/443 개방
#
# 사용:
#   sudo bash deploy/ssl-duckdns.sh you@email.com

set -euo pipefail

DOMAIN="auto-trade-view.duckdns.org"
EMAIL="${1:-}"
SERVICE_NAME="autotrade"
APP_DIR="${APP_DIR:-/opt/auto-trade}"

if [[ -z "$EMAIL" ]]; then
  echo "Usage: sudo bash deploy/ssl-duckdns.sh <EMAIL>"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "root(sudo)로 실행하세요."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx

mkdir -p /var/www/certbot

# HTTP 서버 블록 (ACME + 프록시) — SSL 전 단계
cat > /etc/nginx/sites-available/"$SERVICE_NAME" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location /static/ {
        alias ${APP_DIR}/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF

ln -sfn /etc/nginx/sites-available/"$SERVICE_NAME" /etc/nginx/sites-enabled/"$SERVICE_NAME"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> DuckDNS 해석 확인"
RESOLVED="$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || true)"
echo "  $DOMAIN → ${RESOLVED:-<없음>}"
if [[ -z "$RESOLVED" ]]; then
  echo "DNS가 아직 안 붙었습니다. DuckDNS에 IP 등록 후 다시 실행하세요."
  exit 1
fi

echo "==> certbot (Let's Encrypt)"
certbot --nginx \
  -d "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos \
  --non-interactive \
  --redirect

nginx -t
systemctl reload nginx

# Django ALLOWED_HOSTS 힌트
if [[ -f "$APP_DIR/.env" ]]; then
  if grep -q '^ALLOWED_HOSTS=' "$APP_DIR/.env"; then
    sed -i "s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=$DOMAIN|" "$APP_DIR/.env"
  else
    echo "ALLOWED_HOSTS=$DOMAIN" >> "$APP_DIR/.env"
  fi
  systemctl restart "$SERVICE_NAME" 2>/dev/null || true
fi

echo
echo "========================================"
echo " SSL OK: https://${DOMAIN}"
echo " 문서  : https://${DOMAIN}/api/docs/"
echo " 갱신테스트: sudo certbot renew --dry-run"
echo "========================================"
