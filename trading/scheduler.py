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
    
    # 종목 크롤링 작업 등록 (설정에서 지정한 간격마다 실행, 기본값: 1시간)
    crawl_interval_hours = getattr(settings, 'SYMBOL_CRAWL_INTERVAL_HOURS', 1)
    scheduler.add_job(
        crawl_symbols_job,
        trigger=IntervalTrigger(hours=crawl_interval_hours),
        id='crawl_symbols',
        name='종목 크롤링',
        replace_existing=True,
    )
    
    # 일일 실현 손익 계산 작업 등록 (매일 자정에 실행)
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        calculate_daily_profit_job,
        trigger=CronTrigger(hour=0, minute=0),  # 매일 자정
        id='calculate_daily_profit',
        name='일일 실현 손익 계산',
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
    from .notifications import notify_scheduler
    from .models import Order, OrderStatus
    
    try:
        # 처리 전 대기중인 주문 수
        before_count = Order.objects.filter(status=OrderStatus.PENDING).count()
        
        # 주문 처리
        processed_count = process_orders()
        
        # 처리 후 대기중인 주문 수
        after_count = Order.objects.filter(status=OrderStatus.PENDING).count()
        
        # 알림 전송 (주문이 처리된 경우에만)
        if processed_count > 0:
            message = f"✅ 주문 처리 완료\n\n"
            message += f"처리된 주문: {processed_count}개\n"
            message += f"대기중인 주문: {after_count}개"
            notify_scheduler(message, job_name="주문 처리")
        elif before_count > 0:
            # 처리할 주문이 있었지만 처리되지 않은 경우
            message = f"⚠️ 주문 처리 확인 필요\n\n"
            message += f"대기중인 주문: {before_count}개\n"
            message += f"처리된 주문: 0개"
            notify_scheduler(message, job_name="주문 처리")
    except Exception as e:
        error_msg = f"주문 처리 작업 실행 중 오류: {str(e)}"
        logger.error(error_msg)
        notify_scheduler(
            f"❌ 주문 처리 실패\n\n오류: {str(e)}",
            job_name="주문 처리"
        )


def crawl_symbols_job():
    """종목 크롤링 작업 (스케줄러에서 호출)"""
    from .crawlers import crawl_all_symbols
    from .notifications import notify_scheduler
    from .models import Symbol
    
    try:
        # 크롤링 전 종목 수
        before_count = Symbol.objects.filter(is_delisted=False).count()
        
        # 크롤링 실행
        crawl_all_symbols()
        
        # 크롤링 후 종목 수
        after_count = Symbol.objects.filter(is_delisted=False).count()
        delisted_count = Symbol.objects.filter(is_delisted=True).count()
        
        message = f"✅ 종목 크롤링 완료\n\n"
        message += f"상장 종목: {after_count}개\n"
        message += f"상장폐지 종목: {delisted_count}개\n"
        if after_count != before_count:
            message += f"변동: {after_count - before_count:+d}개"
        
        notify_scheduler(message, job_name="종목 크롤링")
    except Exception as e:
        error_msg = f"종목 크롤링 작업 실행 중 오류: {str(e)}"
        logger.error(error_msg)
        notify_scheduler(
            f"❌ 종목 크롤링 실패\n\n오류: {str(e)}",
            job_name="종목 크롤링"
        )


def calculate_daily_profit_job():
    """일일 실현 손익 계산 작업 (스케줄러에서 호출)"""
    from .profit_calculator import ProfitCalculator
    from .notifications import notify_scheduler
    from django.utils import timezone
    from datetime import timedelta
    
    try:
        # 전날 날짜 계산
        yesterday = timezone.now().date() - timedelta(days=1)
        
        updated_count = ProfitCalculator.update_all_accounts_daily_profit(yesterday)
        
        message = f"✅ 일일 실현 손익 계산 완료\n\n"
        message += f"대상 날짜: {yesterday}\n"
        message += f"업데이트된 계좌: {updated_count}개"
        
        notify_scheduler(message, job_name="일일 실현 손익 계산")
    except Exception as e:
        error_msg = f"일일 실현 손익 계산 작업 실행 중 오류: {str(e)}"
        logger.error(error_msg)
        notify_scheduler(
            f"❌ 일일 실현 손익 계산 실패\n\n오류: {str(e)}",
            job_name="일일 실현 손익 계산"
        )

