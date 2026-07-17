from django.apps import AppConfig


class TradingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trading'

    def ready(self):
        """앱이 준비되면 스케줄러 시작 및 시그널 등록"""
        import os
        import sys

        # 시그널은 항상 등록
        import trading.signals  # noqa

        if not self._should_start_background_services(sys.argv, os.environ):
            return

        # Telegram 봇 초기화
        try:
            from .notifications import init_telegram_bots
            init_telegram_bots()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Telegram 봇 초기화 실패: {str(e)}")

        # 스케줄러 시작 (gunicorn / runserver 자식 프로세스)
        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"스케줄러 시작 실패: {str(e)}")

    @staticmethod
    def _should_start_background_services(argv, environ):
        """서버 프로세스에서만 스케줄러/텔레그램을 시작한다."""
        # manage.py 로 실행된 경우
        if argv and argv[0].endswith('manage.py'):
            command = argv[1] if len(argv) > 1 else ''
            # runserver 리로더 부모 프로세스는 스킵, 자식만 시작
            if command == 'runserver':
                return environ.get('RUN_MAIN') == 'true'
            # migrate, test, shell 등에서는 시작하지 않음
            return False

        # gunicorn / uwsgi 등 WSGI 서버에서는 시작
        return True
