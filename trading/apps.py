from django.apps import AppConfig


class TradingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trading'
    
    def ready(self):
        """앱이 준비되면 스케줄러 시작 및 시그널 등록"""
        import os
        # 마이그레이션 중에는 스케줄러를 시작하지 않음
        if os.environ.get('RUN_MAIN') != 'true':
            return
        
        # Telegram 봇 초기화
        try:
            from .notifications import init_telegram_bots
            init_telegram_bots()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Telegram 봇 초기화 실패: {str(e)}")
        
        # 시그널 등록
        import trading.signals  # noqa
        
        # 스케줄러 시작
        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"스케줄러 시작 실패: {str(e)}")