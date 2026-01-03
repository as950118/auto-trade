"""
스케줄러 설정
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django_apscheduler.jobstores import DjangoJobStore, register_events
from django.conf import settings
from django_apscheduler.models import DjangoJobExecution
import sys

logger = logging.getLogger(__name__)


def start_scheduler():
    """스케줄러 시작"""
    scheduler = BackgroundScheduler()
    scheduler.add_jobstore(DjangoJobStore(), "default")
    
    # 주문 처리 작업 등록 (설정에서 지정한 간격마다 실행)
    interval_minutes = getattr(settings, 'ORDER_PROCESSING_INTERVAL_MINUTES', 1)
    scheduler.add_job(
        process_orders_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id='process_orders',
        name='주문 처리',
        replace_existing=True,
    )
    
    register_events(scheduler)
    
    try:
        logger.info("스케줄러 시작...")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료...")
        scheduler.shutdown()


def process_orders_job():
    """주문 처리 작업 (스케줄러에서 호출)"""
    from .tasks import process_orders
    try:
        process_orders()
    except Exception as e:
        logger.error(f"주문 처리 작업 실행 중 오류: {str(e)}")

