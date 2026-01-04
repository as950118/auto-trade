"""
스케줄러 및 태스크 테스트
"""
from django.test import TestCase
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock, call
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone

from .models import (
    Broker, Account, Symbol, Order, OrderStatus, 
    DailyRealizedProfit, Country, Currency
)
from .scheduler import (
    process_orders_job,
    crawl_symbols_job,
    update_accounts_info_job,
    calculate_daily_profit_job
)
from .tasks import (
    process_orders,
    update_accounts_info,
    update_account_info
)


class SchedulerJobTestCase(TestCase):
    """스케줄러 작업 테스트"""
    
    def setUp(self):
        """테스트 데이터 설정"""
        # 사용자 생성
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # 브로커 생성
        self.broker = Broker.objects.create(
            code='UPBIT',
            name='Upbit',
            country=Country.KOREA,
            is_crypto_exchange=True
        )
        
        # 계좌 생성
        self.account = Account.objects.create(
            user=self.user,
            broker=self.broker,
            account_number='test-account-001',
            account_password='test-password',
            api_key='test-api-key',
            api_secret='test-api-secret',
            cash_balance=Decimal('1000000'),
            stock_value=Decimal('500000'),
            total_assets=Decimal('1500000')
        )
        
        # 종목 생성
        self.symbol = Symbol.objects.create(
            ticker='BTC-KRW',
            name='비트코인',
            currency=Currency.KRW,
            broker=self.broker,
            is_crypto=True
        )
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.tasks.process_orders')
    def test_process_orders_job_success(self, mock_process_orders, mock_notify):
        """주문 처리 작업 성공 테스트"""
        # 대기중인 주문 생성
        order = Order.objects.create(
            account=self.account,
            symbol=self.symbol,
            side='BUY',
            order_type='MARKET',
            quantity=Decimal('0.001'),
            status=OrderStatus.PENDING
        )
        
        # 모킹 설정
        mock_process_orders.return_value = 1
        
        # 작업 실행
        process_orders_job()
        
        # 검증
        mock_process_orders.assert_called_once()
        mock_notify.assert_called_once()
        
        # 알림 메시지 확인
        call_args = mock_notify.call_args
        self.assertIn('주문 처리 완료', call_args[0][0])
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.tasks.process_orders')
    def test_process_orders_job_no_orders(self, mock_process_orders, mock_notify):
        """주문이 없을 때 작업 테스트"""
        mock_process_orders.return_value = 0
        
        process_orders_job()
        
        mock_process_orders.assert_called_once()
        # 주문이 없으면 알림이 전송되지 않음
        mock_notify.assert_not_called()
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.tasks.process_orders')
    def test_process_orders_job_exception(self, mock_process_orders, mock_notify):
        """주문 처리 작업 예외 처리 테스트"""
        mock_process_orders.side_effect = Exception("처리 실패")
        
        process_orders_job()
        
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        self.assertIn('주문 처리 실패', call_args[0][0])
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.crawlers.crawl_all_symbols')
    def test_crawl_symbols_job_success(self, mock_crawl, mock_notify):
        """종목 크롤링 작업 성공 테스트"""
        mock_crawl.return_value = None
        
        crawl_symbols_job()
        
        mock_crawl.assert_called_once()
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        self.assertIn('종목 크롤링 완료', call_args[0][0])
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.crawlers.crawl_all_symbols')
    def test_crawl_symbols_job_exception(self, mock_crawl, mock_notify):
        """종목 크롤링 작업 예외 처리 테스트"""
        mock_crawl.side_effect = Exception("크롤링 실패")
        
        crawl_symbols_job()
        
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        self.assertIn('종목 크롤링 실패', call_args[0][0])
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.tasks.update_accounts_info')
    def test_update_accounts_info_job_success(self, mock_update, mock_notify):
        """계좌 정보 업데이트 작업 성공 테스트"""
        mock_update.return_value = 1
        
        update_accounts_info_job()
        
        mock_update.assert_called_once()
        # 모든 계좌가 업데이트되면 알림이 전송되지 않음
        mock_notify.assert_not_called()
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.tasks.update_accounts_info')
    def test_update_accounts_info_job_partial_failure(self, mock_update, mock_notify):
        """계좌 정보 업데이트 부분 실패 테스트"""
        # 전체 계좌 1개, 업데이트 성공 0개 (일부 실패)
        mock_update.return_value = 0
        
        update_accounts_info_job()
        
        mock_update.assert_called_once()
        # 일부 실패 시 알림 전송
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        self.assertIn('계좌 정보 업데이트 완료', call_args[0][0])
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.tasks.update_accounts_info')
    def test_update_accounts_info_job_exception(self, mock_update, mock_notify):
        """계좌 정보 업데이트 작업 예외 처리 테스트"""
        mock_update.side_effect = Exception("업데이트 실패")
        
        update_accounts_info_job()
        
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        self.assertIn('계좌 정보 업데이트 실패', call_args[0][0])
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.profit_calculator.ProfitCalculator.update_all_accounts_daily_profit')
    def test_calculate_daily_profit_job_success(self, mock_calculate, mock_notify):
        """일일 실현 손익 계산 작업 성공 테스트"""
        mock_calculate.return_value = 1
        
        calculate_daily_profit_job()
        
        mock_calculate.assert_called_once()
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        self.assertIn('일일 실현 손익 계산 완료', call_args[0][0])
    
    @patch('trading.scheduler.notify_scheduler')
    @patch('trading.profit_calculator.ProfitCalculator.update_all_accounts_daily_profit')
    def test_calculate_daily_profit_job_exception(self, mock_calculate, mock_notify):
        """일일 실현 손익 계산 작업 예외 처리 테스트"""
        mock_calculate.side_effect = Exception("계산 실패")
        
        calculate_daily_profit_job()
        
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        self.assertIn('일일 실현 손익 계산 실패', call_args[0][0])


class TasksTestCase(TestCase):
    """태스크 함수 테스트"""
    
    def setUp(self):
        """테스트 데이터 설정"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.broker = Broker.objects.create(
            code='UPBIT',
            name='Upbit',
            country=Country.KOREA,
            is_crypto_exchange=True
        )
        
        self.account = Account.objects.create(
            user=self.user,
            broker=self.broker,
            account_number='test-account-001',
            account_password='test-password',
            api_key='test-api-key',
            api_secret='test-api-secret'
        )
        
        self.symbol = Symbol.objects.create(
            ticker='BTC-KRW',
            name='비트코인',
            currency=Currency.KRW,
            broker=self.broker,
            is_crypto=True
        )
    
    @patch('trading.tasks.get_broker_client')
    def test_update_account_info_success(self, mock_get_client):
        """계좌 정보 업데이트 성공 테스트"""
        # 모킹된 클라이언트 생성
        mock_client = MagicMock()
        mock_client.get_account_info.return_value = {
            'success': True,
            'cash_balance': Decimal('2000000'),
            'stock_value': Decimal('1000000'),
            'total_assets': Decimal('3000000'),
            'data': {}
        }
        mock_get_client.return_value = mock_client
        
        # 계좌 정보 업데이트
        update_account_info(self.account)
        
        # 검증
        self.account.refresh_from_db()
        self.assertEqual(self.account.cash_balance, Decimal('2000000'))
        self.assertEqual(self.account.stock_value, Decimal('1000000'))
        self.assertEqual(self.account.total_assets, Decimal('3000000'))
        mock_get_client.assert_called_once_with(self.account)
        mock_client.get_account_info.assert_called_once()
    
    @patch('trading.tasks.get_broker_client')
    def test_update_account_info_failure(self, mock_get_client):
        """계좌 정보 업데이트 실패 테스트"""
        mock_client = MagicMock()
        mock_client.get_account_info.return_value = {
            'success': False,
            'error': 'API 오류'
        }
        mock_get_client.return_value = mock_client
        
        # 예외 발생 확인
        with self.assertRaises(Exception):
            update_account_info(self.account)
    
    @patch('trading.tasks.get_broker_client')
    def test_update_account_info_no_api_key(self, mock_get_client):
        """API 키가 없을 때 테스트"""
        self.account.api_key = None
        self.account.save()
        
        mock_get_client.side_effect = ValueError("API 키가 필요합니다")
        
        # 예외가 발생하지 않고 스킵되어야 함
        update_account_info(self.account)
        
        # ValueError는 로그만 남기고 예외를 발생시키지 않음
        mock_get_client.assert_called_once_with(self.account)
    
    @patch('trading.tasks.update_account_info')
    def test_update_accounts_info_multiple_accounts(self, mock_update):
        """여러 계좌 정보 업데이트 테스트"""
        # 추가 계좌 생성
        account2 = Account.objects.create(
            user=self.user,
            broker=self.broker,
            account_number='test-account-002',
            account_password='test-password',
            api_key='test-api-key-2',
            api_secret='test-api-secret-2'
        )
        
        mock_update.return_value = None
        
        updated_count = update_accounts_info()
        
        # 두 계좌 모두 업데이트되어야 함
        self.assertEqual(mock_update.call_count, 2)
        self.assertEqual(updated_count, 2)
    
    @patch('trading.tasks.update_account_info')
    def test_update_accounts_info_partial_failure(self, mock_update):
        """일부 계좌 업데이트 실패 테스트"""
        account2 = Account.objects.create(
            user=self.user,
            broker=self.broker,
            account_number='test-account-002',
            account_password='test-password',
            api_key='test-api-key-2',
            api_secret='test-api-secret-2'
        )
        
        # 첫 번째는 성공, 두 번째는 실패
        mock_update.side_effect = [None, Exception("업데이트 실패")]
        
        updated_count = update_accounts_info()
        
        # 첫 번째만 성공
        self.assertEqual(updated_count, 1)
        self.assertEqual(mock_update.call_count, 2)
    
    @patch('trading.tasks.get_broker_client')
    def test_process_orders_with_pending_orders(self, mock_get_client):
        """대기중인 주문 처리 테스트"""
        # 대기중인 주문 생성
        order = Order.objects.create(
            account=self.account,
            symbol=self.symbol,
            side='BUY',
            order_type='MARKET',
            quantity=Decimal('0.001'),
            status=OrderStatus.PENDING
        )
        
        # 모킹된 클라이언트
        mock_client = MagicMock()
        mock_client.place_order.return_value = {
            'success': True,
            'order_id': 'external-order-123',
            'data': {}
        }
        mock_get_client.return_value = mock_client
        
        processed_count = process_orders()
        
        # 검증
        self.assertEqual(processed_count, 1)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(order.external_order_id, 'external-order-123')
    
    @patch('trading.tasks.get_broker_client')
    def test_process_orders_with_rejected_order(self, mock_get_client):
        """주문 거부 테스트"""
        # 매수 비활성화된 계좌
        self.account.buy_enabled = False
        self.account.save()
        
        order = Order.objects.create(
            account=self.account,
            symbol=self.symbol,
            side='BUY',
            order_type='MARKET',
            quantity=Decimal('0.001'),
            status=OrderStatus.PENDING
        )
        
        processed_count = process_orders()
        
        # 검증
        self.assertEqual(processed_count, 1)  # 처리 시도는 했지만 거부됨
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.REJECTED)
        mock_get_client.assert_not_called()  # 클라이언트가 호출되지 않음
    
    @patch('trading.tasks.get_broker_client')
    def test_process_orders_with_investment_limit(self, mock_get_client):
        """투자 상한선 초과 주문 테스트"""
        self.account.investment_limit = Decimal('100000')
        self.account.save()
        
        # 상한선 초과 주문
        order = Order.objects.create(
            account=self.account,
            symbol=self.symbol,
            side='BUY',
            order_type='LIMIT',
            quantity=Decimal('1'),
            price=Decimal('200000'),  # 200,000원 (상한선 초과)
            status=OrderStatus.PENDING
        )
        
        processed_count = process_orders()
        
        # 검증
        self.assertEqual(processed_count, 1)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.REJECTED)
        mock_get_client.assert_not_called()
