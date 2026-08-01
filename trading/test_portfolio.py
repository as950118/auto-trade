"""
Portfolio + PortfolioHolding + PortfolioLink: 비중 검증, 리밸런싱, 구독 권한 테스트
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    Account,
    Broker,
    Country,
    Currency,
    Holding,
    Order,
    OrderSide,
    Portfolio,
    PortfolioHolding,
    PortfolioLink,
    PortfolioVisibility,
    Symbol,
)
from .services.portfolio import rebalance_link, rebalance_portfolio


class PortfolioRebalanceTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='pass123')
        self.subscriber = User.objects.create_user(username='sub', password='pass123')
        self.broker = Broker.objects.create(
            code='UPBIT2', name='Upbit2', country=Country.KOREA, is_crypto_exchange=True
        )
        self.account = Account.objects.create(
            user=self.subscriber,
            broker=self.broker,
            api_key='k',
            api_secret='s',
            cash_balance_krw=Decimal('10000000'),
            buy_enabled=True,
            sell_enabled=True,
        )
        self.btc = Symbol.objects.create(
            ticker='BTC-KRW', name='Bitcoin', currency=Currency.KRW, broker=self.broker, is_crypto=True,
        )
        self.eth = Symbol.objects.create(
            ticker='ETH-KRW', name='Ethereum', currency=Currency.KRW, broker=self.broker, is_crypto=True,
        )
        self.usdt_symbol = Symbol.objects.create(
            ticker='USDT-KRW', name='Tether', currency=Currency.USD, broker=self.broker, is_crypto=True,
        )
        self.portfolio = Portfolio.objects.create(owner=self.owner, title='Balanced 60/40')
        PortfolioHolding.objects.create(
            portfolio=self.portfolio, symbol=self.btc, target_weight_percent=Decimal('60')
        )
        PortfolioHolding.objects.create(
            portfolio=self.portfolio, symbol=self.eth, target_weight_percent=Decimal('40')
        )
        Holding.objects.create(
            account=self.account, symbol=self.btc, quantity=Decimal('0'), current_price=Decimal('100000000'),
        )
        Holding.objects.create(
            account=self.account, symbol=self.eth, quantity=Decimal('0'), current_price=Decimal('5000000'),
        )
        self.link = PortfolioLink.objects.create(
            portfolio=self.portfolio,
            account=self.account,
            seed_amount=Decimal('1000000'),
            seed_currency=Currency.KRW,
        )

    def test_weight_sum_over_100_rejected_via_holdings_action(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        resp = client.put(
            f'/api/portfolios/{self.portfolio.id}/holdings/',
            [
                {'symbol_id': self.btc.id, 'target_weight_percent': '70'},
                {'symbol_id': self.eth.id, 'target_weight_percent': '40'},
            ],
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_currency_mismatch_rejected_via_holdings_action(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        resp = client.put(
            f'/api/portfolios/{self.portfolio.id}/holdings/',
            [
                {'symbol_id': self.btc.id, 'target_weight_percent': '50'},
                {'symbol_id': self.usdt_symbol.id, 'target_weight_percent': '50'},
            ],
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_link_creation_triggers_initial_buy_orders(self):
        # setUp already creates the link (which internally does not auto-call rebalance
        # since we constructed the model directly); call the service explicitly here.
        created = rebalance_link(self.link)
        self.assertEqual(created, 2)
        btc_order = Order.objects.get(account=self.account, symbol=self.btc)
        eth_order = Order.objects.get(account=self.account, symbol=self.eth)
        self.assertEqual(btc_order.side, OrderSide.BUY)
        self.assertEqual(eth_order.side, OrderSide.BUY)
        # 60% of 1,000,000 = 600,000 / 100,000,000 = 0.006 BTC
        self.assertEqual(btc_order.quantity, Decimal('0.00600000'))
        # 40% of 1,000,000 = 400,000 / 5,000,000 = 0.08 ETH
        self.assertEqual(eth_order.quantity, Decimal('0.08000000'))

    def test_rebalance_creates_sell_when_overweight(self):
        rebalance_link(self.link)
        # simulate BTC holding growing beyond target weight
        holding = Holding.objects.get(account=self.account, symbol=self.btc)
        holding.quantity = Decimal('0.02')
        holding.total_value = holding.quantity * holding.current_price
        holding.save()

        created = rebalance_link(self.link)
        self.assertGreaterEqual(created, 1)
        sell_orders = Order.objects.filter(account=self.account, symbol=self.btc, side=OrderSide.SELL)
        self.assertTrue(sell_orders.exists())

    def test_sell_quantity_never_exceeds_holding_quantity(self):
        # total_value is derived (quantity * current_price) in Holding.save(), so an
        # overweight position is simulated by growing quantity, not by forcing total_value.
        holding = Holding.objects.get(account=self.account, symbol=self.btc)
        holding.quantity = Decimal('0.05')
        holding.save()

        rebalance_link(self.link)
        sell_order = Order.objects.filter(account=self.account, symbol=self.btc, side=OrderSide.SELL).first()
        self.assertIsNotNone(sell_order)
        self.assertLessEqual(sell_order.quantity, holding.quantity)

    def test_private_portfolio_link_permission(self):
        other_user = User.objects.create_user(username='other', password='pass123')
        other_account = Account.objects.create(
            user=other_user, broker=self.broker, api_key='k2', api_secret='s2',
        )
        client = APIClient()
        client.force_authenticate(user=other_user)
        resp = client.post(
            '/api/portfolio-links/',
            {
                'portfolio_id': self.portfolio.id,
                'account_id': other_account.id,
                'seed_amount': '500000',
                'seed_currency': Currency.KRW,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_public_portfolio_link_creates_orders(self):
        self.portfolio.visibility = PortfolioVisibility.PUBLIC
        self.portfolio.owner.is_staff = True
        self.portfolio.owner.save()
        self.portfolio.save()

        other_user = User.objects.create_user(username='other2', password='pass123')
        other_account = Account.objects.create(
            user=other_user, broker=self.broker, api_key='k3', api_secret='s3',
        )
        Holding.objects.create(
            account=other_account, symbol=self.btc, quantity=Decimal('0'), current_price=Decimal('100000000'),
        )
        Holding.objects.create(
            account=other_account, symbol=self.eth, quantity=Decimal('0'), current_price=Decimal('5000000'),
        )

        client = APIClient()
        client.force_authenticate(user=other_user)
        resp = client.post(
            '/api/portfolio-links/',
            {
                'portfolio_id': self.portfolio.id,
                'account_id': other_account.id,
                'seed_amount': '200000',
                'seed_currency': Currency.KRW,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Order.objects.filter(account=other_account).exists())
