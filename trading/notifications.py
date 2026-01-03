"""
Telegram 알림 모듈
"""
import logging
import requests
from typing import Optional
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class Telegram:
    """Telegram 봇 알림 클래스"""
    
    def __init__(self, token: str, chat_id: str, name: str = ""):
        self.token = token
        self.chat_id = chat_id
        self.name = name
        self.base_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """메시지 전송"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            
            response = requests.post(url, json=data, timeout=10)
            
            if response.status_code == 200:
                logger.debug(f"Telegram 메시지 전송 성공 ({self.name}): {message[:50]}...")
                return True
            else:
                logger.error(f"Telegram 메시지 전송 실패 ({self.name}): {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Telegram 메시지 전송 중 오류 발생 ({self.name}): {str(e)}")
            return False
    
    def send_notification(self, title: str, message: str, parse_mode: str = "HTML") -> bool:
        """알림 메시지 전송 (제목 포함)"""
        formatted_message = f"<b>{title}</b>\n\n{message}"
        return self.send_message(formatted_message, parse_mode)


# 전역 Telegram 인스턴스 (settings에서 초기화)
new_customer_telegram: Optional[Telegram] = None
server_scheduler_telegram: Optional[Telegram] = None


def init_telegram_bots():
    """Telegram 봇 초기화"""
    global new_customer_telegram, server_scheduler_telegram
    
    try:
        # 신규 고객 알림용 봇
        new_customer_config = getattr(settings, 'TELEGRAM_NEW_CUSTOMER', None)
        if new_customer_config:
            new_customer_telegram = Telegram(
                token=new_customer_config.get('token'),
                chat_id=new_customer_config.get('chat_id'),
                name="new_customer"
            )
            logger.info("신규 고객 Telegram 봇 초기화 완료")
        
        # 스케줄러 알림용 봇
        server_scheduler_config = getattr(settings, 'TELEGRAM_SERVER_SCHEDULER', None)
        if server_scheduler_config:
            server_scheduler_telegram = Telegram(
                token=server_scheduler_config.get('token'),
                chat_id=server_scheduler_config.get('chat_id'),
                name="server_scheduler"
            )
            logger.info("스케줄러 Telegram 봇 초기화 완료")
            
    except Exception as e:
        logger.error(f"Telegram 봇 초기화 실패: {str(e)}")


def notify_new_customer(username: str, email: str = ""):
    """신규 가입 알림"""
    global new_customer_telegram
    
    if not new_customer_telegram:
        logger.warning("신규 고객 Telegram 봇이 초기화되지 않았습니다.")
        return False
    
    message = f"👤 <b>신규 가입</b>\n\n"
    message += f"사용자명: {username}\n"
    if email:
        message += f"이메일: {email}\n"
    message += f"가입 시간: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    return new_customer_telegram.send_notification("신규 가입 알림", message)


def notify_account_registered(username: str, broker_name: str, account_number: str):
    """계좌 등록 알림"""
    global new_customer_telegram
    
    if not new_customer_telegram:
        logger.warning("신규 고객 Telegram 봇이 초기화되지 않았습니다.")
        return False
    
    message = f"💼 <b>계좌 등록</b>\n\n"
    message += f"사용자명: {username}\n"
    message += f"브로커: {broker_name}\n"
    message += f"계좌번호: {account_number}\n"
    message += f"등록 시간: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    return new_customer_telegram.send_notification("계좌 등록 알림", message)


def notify_scheduler(message: str, job_name: str = ""):
    """스케줄러 알림"""
    global server_scheduler_telegram
    
    if not server_scheduler_telegram:
        logger.warning("스케줄러 Telegram 봇이 초기화되지 않았습니다.")
        return False
    
    title = f"⏰ 스케줄러 알림"
    if job_name:
        title += f" - {job_name}"
    
    formatted_message = f"{message}\n\n"
    formatted_message += f"시간: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    return server_scheduler_telegram.send_notification(title, formatted_message)

