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

# APP_DIR은 보통 setup.sh를 실행한 로그인 유저(예: ubuntu) 소유로 남아있는데,
# 이후 배포 단계들은 APP_USER(www-data)로 git pull / migrate / collectstatic을
# 수행한다. 소유자가 섞여 있으면 ".git/FETCH_HEAD: Permission denied" 같은
# 에러나 git의 "dubious ownership" 거부가 발생한다. 이 스크립트는 이미
# root로만 실행되므로(위 체크), 매 배포마다 소유권을 APP_USER로 통일해서
# 이런 드리프트를 자동으로 정리한다.
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# --system 설정은 유저별 $HOME/.gitconfig 유무와 무관하게 root/APP_USER
# 모두에 적용되므로, 유저마다 safe.directory를 따로 등록할 필요가 없다.
git config --system --add safe.directory "$APP_DIR"

if [[ -d .git ]]; then
  echo "==> git pull"
  # www-data의 기본 $HOME(/var/www)은 보통 root 소유라 www-data 자신이 쓸 수
  # 없다. git이 전역 설정을 읽거나 쓰려고 할 때 이 경로를 건드리다 실패하지
  # 않도록, 이미 www-data 소유로 chown해둔 APP_DIR을 HOME으로 지정한다.
  sudo -u "$APP_USER" env HOME="$APP_DIR" git pull --ff-only
fi

echo "==> pip install"
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install gunicorn

echo "==> migrate / collectstatic"
sudo -u "$APP_USER" env HOME="$APP_DIR" bash -c "cd '$APP_DIR' && .venv/bin/python manage.py migrate --noinput"
sudo -u "$APP_USER" env HOME="$APP_DIR" bash -c "cd '$APP_DIR' && .venv/bin/python manage.py collectstatic --noinput"

echo "==> restart"
systemctl restart "$SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME" || true

echo "==> health check"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health/}"
HEALTH_RETRIES="${HEALTH_RETRIES:-10}"
HEALTH_INTERVAL_SEC="${HEALTH_INTERVAL_SEC:-3}"

healthy=0
for i in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -fsS "$HEALTH_URL" >/tmp/autotrade-health-check.json 2>/dev/null; then
    healthy=1
    break
  fi
  echo "  attempt $i/$HEALTH_RETRIES failed, retrying in ${HEALTH_INTERVAL_SEC}s..."
  sleep "$HEALTH_INTERVAL_SEC"
done

if [[ "$healthy" -ne 1 ]]; then
  echo "!! health check failed after $HEALTH_RETRIES attempts ($HEALTH_URL)"
  echo "!! deployment likely broken, check: journalctl -u $SERVICE_NAME -n 100 --no-pager"
  exit 1
fi

echo "  health check ok: $(cat /tmp/autotrade-health-check.json)"
echo "done."
