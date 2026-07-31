#!/usr/bin/env bash
# 코드 갱신 후 재배포 (migrate / collectstatic / restart)
#
# 사용법 (프로젝트 루트에서):
#   sudo bash deploy/update.sh
#   sudo APP_DIR=/opt/auto-trade bash deploy/update.sh

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/auto-trade}"
APP_USER="${APP_USER:-www-data}"
SERVICE_NAME="autotrade"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "root(sudo)로 실행하세요."
  exit 1
fi

cd "$APP_DIR"

if [[ -d .git ]]; then
  # APP_DIR이 보통 setup.sh를 실행한 유저(예: ubuntu) 소유라, 이 스크립트를
  # 실행하는 root/APP_USER와 소유자가 달라 git이 "dubious ownership"으로
  # pull을 거부할 수 있다. 두 유저 모두에 대해 안전 예외를 등록해둔다.
  git config --global --add safe.directory "$APP_DIR"
  sudo -u "$APP_USER" git config --global --add safe.directory "$APP_DIR"

  echo "==> git pull"
  sudo -u "$APP_USER" git pull --ff-only || git pull --ff-only
fi

echo "==> pip install"
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install gunicorn

echo "==> migrate / collectstatic"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python manage.py migrate --noinput"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python manage.py collectstatic --noinput"

echo "==> restart"
systemctl restart "$SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME" || true
echo "done."
