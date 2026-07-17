#!/usr/bin/env bash
# Ubuntu 24 — auto-trade 백엔드 + nginx + Let's Encrypt SSL 최초 설치
#
# 사전 조건:
#   1) 도메인 A레코드가 이 서버 IP를 가리킬 것
#   2) 프로젝트 코드가 APP_DIR에 있을 것 (기본: /opt/auto-trade)
#   3) root 또는 sudo로 실행
#
# 사용법:
#   sudo bash deploy/setup.sh api.example.com you@example.com
#   sudo APP_DIR=/home/ubuntu/auto-trade bash deploy/setup.sh api.example.com you@example.com

set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
APP_DIR="${APP_DIR:-/opt/auto-trade}"
APP_USER="${APP_USER:-www-data}"
SERVICE_NAME="autotrade"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Usage: sudo bash deploy/setup.sh <DOMAIN> <EMAIL>"
  echo "Example: sudo bash deploy/setup.sh api.example.com admin@example.com"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "root(sudo)로 실행하세요."
  exit 1
fi

if [[ ! -f "$APP_DIR/manage.py" ]]; then
  echo "APP_DIR=$APP_DIR 에 Django 프로젝트가 없습니다."
  echo "먼저 코드를 $APP_DIR 로 복사/클론한 뒤 다시 실행하세요."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> [1/8] apt 패키지"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  python3 python3-venv python3-dev \
  build-essential libpq-dev \
  nginx certbot python3-certbot-nginx \
  curl

echo "==> [2/8] 디렉터리 / 로그"
mkdir -p /var/log/autotrade /var/www/certbot
chown -R "$APP_USER:$APP_USER" /var/log/autotrade
chown -R "$APP_USER:$APP_USER" "$APP_DIR" || true

echo "==> [3/8] venv + 의존성"
cd "$APP_DIR"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install gunicorn

echo "==> [4/8] .env"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$SCRIPT_DIR/env.example" "$APP_DIR/.env"
  # ALLOWED_HOSTS 에 도메인 넣어 둠
  sed -i "s/^ALLOWED_HOSTS=.*/ALLOWED_HOSTS=$DOMAIN/" "$APP_DIR/.env"
  echo "⚠  $APP_DIR/.env 를 열어 SECRET_KEY / DB_* 를 채운 뒤 Enter"
  read -r _
fi

# .env 필수 키 간단 체크
# shellcheck disable=SC1091
set -a
source "$APP_DIR/.env"
set +a
if [[ -z "${SECRET_KEY:-}" || "$SECRET_KEY" == "change-me-to-a-long-random-string" ]]; then
  echo "SECRET_KEY를 .env에 설정하세요."
  exit 1
fi
if [[ -z "${DB_HOST:-}" ]]; then
  echo "DB_HOST를 .env에 설정하세요."
  exit 1
fi

echo "==> [5/8] migrate / collectstatic"
cd "$APP_DIR"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python manage.py migrate --noinput"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python manage.py collectstatic --noinput"

echo "==> [6/8] systemd (gunicorn workers=1)"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
sed -e "s|/opt/auto-trade|$APP_DIR|g" \
    -e "s|User=www-data|User=$APP_USER|g" \
    -e "s|Group=www-data|Group=$APP_USER|g" \
    "$SCRIPT_DIR/autotrade.service" > "$UNIT_DST"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl --no-pager --full status "$SERVICE_NAME" || true

echo "==> [7/8] nginx (HTTP만 먼저 — certbot용)"
# SSL 발급 전: HTTP + ACME만
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

echo "==> [8/8] Let's Encrypt SSL"
certbot --nginx \
  -d "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos \
  --non-interactive \
  --redirect

nginx -t
systemctl reload nginx

echo
echo "========================================"
echo " 완료"
echo "  URL : https://${DOMAIN}/api/docs/"
echo "  앱  : systemctl status ${SERVICE_NAME}"
echo "  로그: journalctl -u ${SERVICE_NAME} -f"
echo "  갱신: certbot renew --dry-run"
echo "========================================"
