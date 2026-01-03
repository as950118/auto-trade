"""
Django 시그널 핸들러
"""
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Account
from .notifications import notify_new_customer, notify_account_registered, init_telegram_bots

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def user_created_handler(sender, instance, created, **kwargs):
    """신규 사용자 가입 시 알림"""
    if created:
        try:
            notify_new_customer(
                username=instance.username,
                email=instance.email or ""
            )
            logger.info(f"신규 가입 알림 전송: {instance.username}")
        except Exception as e:
            logger.error(f"신규 가입 알림 전송 실패: {str(e)}")


@receiver(post_save, sender=Account)
def account_created_handler(sender, instance, created, **kwargs):
    """계좌 등록 시 알림"""
    if created:
        try:
            notify_account_registered(
                username=instance.user.username,
                broker_name=instance.broker.name,
                account_number=instance.account_number
            )
            logger.info(f"계좌 등록 알림 전송: {instance.user.username} - {instance.broker.name}")
        except Exception as e:
            logger.error(f"계좌 등록 알림 전송 실패: {str(e)}")

