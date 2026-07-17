# Ubuntu 24 백엔드 배포 (nginx + gunicorn + Let's Encrypt)

## 파일
- `setup.sh` — 최초 설치 (패키지, venv, systemd, nginx, SSL)
- `update.sh` — 코드 갱신 후 migrate / restart
- `env.example` — `.env` 템플릿
- `autotrade.service` — systemd 유닛
- `nginx.conf.template` — 참고용 (실제 SSL은 certbot이 설정)

## 서버에서

```bash
# 1) 코드 배치
sudo mkdir -p /opt/auto-trade
sudo chown "$USER:$USER" /opt/auto-trade
# git clone 또는 scp 로 /opt/auto-trade 에 복사

# 2) 도메인 A레코드 → 서버 IP 확인 후
cd /opt/auto-trade
sudo bash deploy/setup.sh api.yourdomain.com you@email.com
# .env 작성 프롬프트에서 SECRET_KEY / DB_* 입력

# 3) 이후 업데이트
sudo bash deploy/update.sh
```

경로를 바꾸려면: `sudo APP_DIR=/home/ubuntu/auto-trade bash deploy/setup.sh ...`
