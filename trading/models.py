from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class Currency(models.TextChoices):
    """화폐단위 ENUM"""
    KRW = 'KRW', '원'
    USD = 'USD', '달러'
    USDT = 'USDT', '테더'
    BTC = 'BTC', '비트코인'
    ETH = 'ETH', '이더리움'


class Country(models.TextChoices):
    """국가 ENUM"""
    KOREA = 'KR', '한국'
    USA = 'US', '미국'
    UK = 'UK', '영국'
    JAPAN = 'JP', '일본'
    CHINA = 'CN', '중국'


class Broker(models.Model):
    """주식브로커(증권사, 암호화폐거래소 등)"""
    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='브로커 코드',
        help_text='시스템에서 사용하는 고유 식별자 (예: UPBIT, BINGX, KIS)'
    )
    name = models.CharField(max_length=100, verbose_name='브로커명')
    country = models.CharField(
        max_length=2,
        choices=Country.choices,
        verbose_name='국가'
    )
    is_crypto_exchange = models.BooleanField(
        default=False,
        verbose_name='암호화폐거래소 여부'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '브로커'
        verbose_name_plural = '브로커들'
        ordering = ['name']

    def __str__(self):
        return self.name


class Symbol(models.Model):
    """종목 (주식 티커, 암호화폐 티커 등)"""
    ticker = models.CharField(max_length=50, verbose_name='티커')
    name = models.CharField(max_length=200, verbose_name='종목명')
    currency = models.CharField(
        max_length=10,
        choices=Currency.choices,
        verbose_name='화폐단위'
    )
    broker = models.ForeignKey(
        Broker,
        on_delete=models.CASCADE,
        related_name='symbols',
        verbose_name='브로커'
    )
    is_crypto = models.BooleanField(default=False, verbose_name='암호화폐 여부')
    is_delisted = models.BooleanField(default=False, verbose_name='상장폐지 여부')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '종목'
        verbose_name_plural = '종목들'
        ordering = ['ticker']
        constraints = [
            models.UniqueConstraint(fields=['broker', 'ticker'], name='unique_broker_ticker'),
        ]

    def __str__(self):
        return f"{self.ticker} ({self.name})"


class Account(models.Model):
    """유저 계좌 정보"""
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='accounts',
        verbose_name='유저'
    )
    broker = models.ForeignKey(
        Broker,
        on_delete=models.CASCADE,
        related_name='accounts',
        verbose_name='브로커'
    )
    account_number = models.CharField(
        max_length=100, 
        blank=True, 
        null=True,
        verbose_name='계좌번호',
        help_text='증권사 계좌번호 (암호화폐 거래소는 불필요)'
    )
    account_password = models.CharField(
        max_length=255, 
        blank=True, 
        null=True,
        verbose_name='계좌비밀번호',
        help_text='증권사 계좌 비밀번호 (암호화폐 거래소는 불필요)'
    )
    api_key = models.CharField(max_length=255, blank=True, null=True, verbose_name='API 키')
    api_secret = models.CharField(max_length=255, blank=True, null=True, verbose_name='API 시크릿')
    
    # API 토큰 정보 (증권사 API에서 발급받은 토큰)
    access_token = models.TextField(blank=True, null=True, verbose_name='액세스 토큰')
    refresh_token = models.TextField(blank=True, null=True, verbose_name='리프레시 토큰')
    token_expires_at = models.DateTimeField(blank=True, null=True, verbose_name='토큰 만료 시간')
    token_issued_at = models.DateTimeField(blank=True, null=True, verbose_name='토큰 발급 시간')
    
    # 자산 정보는 통화별 필드만 사용하고, 기존 필드는 @property로 제공
    # (호환성을 위해 property로 접근 가능하지만, DB에는 저장하지 않음)
    
    # 통화별 자산 정보
    # 원화(KRW) 자산
    cash_balance_krw = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='예수금 (KRW)'
    )
    stock_value_krw = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='보유종목현재가치 (KRW)'
    )
    total_assets_krw = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='총 자산 (KRW)'
    )
    
    # 달러(USD) 자산
    cash_balance_usd = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='예수금 (USD)'
    )
    stock_value_usd = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='보유종목현재가치 (USD)'
    )
    total_assets_usd = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='총 자산 (USD)'
    )

    # 통화별 평가손익 / 수익률 (환율 환산 없이 분리 저장)
    # USDT 포지션은 USD 버킷에 합산 (암호화폐 거래소 호환)
    profit_loss_krw = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name='평가손익 (KRW)'
    )
    profit_rate_krw = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
        verbose_name='수익률 (KRW, %)'
    )
    profit_loss_usd = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name='평가손익 (USD)'
    )
    profit_rate_usd = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
        verbose_name='수익률 (USD, %)'
    )

    # 하위호환: 주 통화(자산이 있는 쪽) 기준 요약값
    profit_rate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
        verbose_name='수익률 (%)'
    )
    profit_loss = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name='평가손익'
    )

    # 거래 제한 설정
    investment_limit = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        verbose_name='투자상한선'
    )
    buy_enabled = models.BooleanField(default=True, verbose_name='매수동작여부')
    sell_enabled = models.BooleanField(default=True, verbose_name='매도동작여부')
    
    # 증권사 특화 설정 (암호화폐거래소의 경우 null)
    unified_margin = models.BooleanField(
        default=False,
        blank=True,
        null=True,
        verbose_name='통합증거금 여부'
    )
    overseas_etp_enabled = models.BooleanField(
        default=False,
        blank=True,
        null=True,
        verbose_name='해외 ETP 거래 가능 여부'
    )
    derivative_etf_enabled = models.BooleanField(
        default=False,
        blank=True,
        null=True,
        verbose_name='파생 ETF 거래 가능 여부'
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '계좌'
        verbose_name_plural = '계좌들'
        ordering = ['-created_at']
        # 계좌번호가 없는 경우도 있으므로 unique_together 조건 수정
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'broker', 'account_number'],
                condition=models.Q(account_number__isnull=False),
                name='unique_account_with_number'
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.broker.name} ({self.account_number})"

    @staticmethod
    def get_usd_krw_rate() -> Decimal:
        from django.conf import settings
        return Decimal(str(getattr(settings, 'USD_KRW_EXCHANGE_RATE', 1400)))

    def _to_krw(self, amount: Decimal, currency: str) -> Decimal:
        """통화 금액을 원화로 환산"""
        amount = amount or Decimal('0')
        if currency in ('USD', 'USDT'):
            return amount * self.get_usd_krw_rate()
        return amount

    @property
    def stock_value(self):
        """보유종목현재가치 (통합 - 원화 기준)"""
        return (self.stock_value_krw or Decimal('0')) + self._to_krw(
            self.stock_value_usd or Decimal('0'), 'USD'
        )
    
    @property
    def cash_balance(self):
        """예수금 (통합 - 원화 기준)"""
        return (self.cash_balance_krw or Decimal('0')) + self._to_krw(
            self.cash_balance_usd or Decimal('0'), 'USD'
        )
    
    @property
    def total_assets(self):
        """총 자산 (통합 - 원화 기준)"""
        return (self.total_assets_krw or Decimal('0')) + self._to_krw(
            self.total_assets_usd or Decimal('0'), 'USD'
        )


class OrderType(models.TextChoices):
    """주문 타입"""
    MARKET = 'MARKET', '시장가'
    LIMIT = 'LIMIT', '지정가'


class OrderSide(models.TextChoices):
    """주문 방향"""
    BUY = 'BUY', '매수'
    SELL = 'SELL', '매도'


class OrderStatus(models.TextChoices):
    """주문 상태"""
    PENDING = 'PENDING', '대기중'
    SUBMITTING = 'SUBMITTING', '전송중'
    PARTIALLY_FILLED = 'PARTIALLY_FILLED', '부분체결'
    FILLED = 'FILLED', '체결완료'
    CANCELLED = 'CANCELLED', '취소됨'
    REJECTED = 'REJECTED', '거부됨'


class Order(models.Model):
    """주문"""
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='orders',
        verbose_name='계좌'
    )
    symbol = models.ForeignKey(
        Symbol,
        on_delete=models.CASCADE,
        related_name='orders',
        verbose_name='종목'
    )
    side = models.CharField(
        max_length=10,
        choices=OrderSide.choices,
        verbose_name='매수/매도'
    )
    order_type = models.CharField(
        max_length=10,
        choices=OrderType.choices,
        verbose_name='주문타입'
    )
    quantity = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        validators=[MinValueValidator(0)],
        verbose_name='수량'
    )
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        verbose_name='지정가 (지정가 주문인 경우 필수)'
    )
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        verbose_name='주문상태'
    )
    filled_quantity = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='체결수량'
    )
    average_filled_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        verbose_name='평균체결가격'
    )
    external_order_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='외부 주문 ID (브로커에서 반환한 주문 ID)'
    )
    portfolio_link = models.ForeignKey(
        'PortfolioLink',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='orders',
        verbose_name='포트폴리오 연동',
        help_text='포트폴리오 리밸런싱으로 생성된 주문인 경우 해당 연동'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='주문일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')
    filled_at = models.DateTimeField(blank=True, null=True, verbose_name='체결일시')

    class Meta:
        verbose_name = '주문'
        verbose_name_plural = '주문들'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.account.user.username} - {self.side} {self.symbol.ticker} {self.quantity} @ {self.price or '시장가'}"

    def is_filled(self):
        """매매가 이뤄졌는지 여부"""
        return self.status == OrderStatus.FILLED

    def save(self, *args, **kwargs):
        # 지정가 주문인 경우 price가 필수
        if self.order_type == OrderType.LIMIT and not self.price:
            raise ValueError("지정가 주문은 가격이 필수입니다.")
        # 체결완료인 경우 filled_at 업데이트
        if self.status == OrderStatus.FILLED and not self.filled_at:
            self.filled_at = timezone.now()
        super().save(*args, **kwargs)


class DailyRealizedProfit(models.Model):
    """일일 실현 손익"""
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='daily_profits',
        verbose_name='계좌'
    )
    date = models.DateField(verbose_name='날짜')
    realized_profit = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name='실현 손익'
    )
    realized_profit_rate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
        verbose_name='실현 손익률 (%)'
    )
    total_buy_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name='총 매수 금액'
    )
    total_sell_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name='총 매도 금액'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '일일 실현 손익'
        verbose_name_plural = '일일 실현 손익들'
        unique_together = ['account', 'date']
        ordering = ['-date']
        indexes = [
            models.Index(fields=['account', 'date']),
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f"{self.account.user.username} - {self.date} - {self.realized_profit}"


class Holding(models.Model):
    """보유 종목"""
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='holdings',
        verbose_name='계좌'
    )
    symbol = models.ForeignKey(
        Symbol,
        on_delete=models.CASCADE,
        related_name='holdings',
        verbose_name='종목'
    )
    quantity = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='보유 수량'
    )
    average_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='평균 매수가'
    )
    current_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='현재가'
    )
    total_value = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='총 가치 (수량 * 현재가)'
    )
    profit_loss = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name='평가 손익'
    )
    profit_rate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
        verbose_name='수익률 (%)'
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')

    class Meta:
        verbose_name = '보유 종목'
        verbose_name_plural = '보유 종목들'
        unique_together = ['account', 'symbol']
        ordering = ['-total_value']
        indexes = [
            models.Index(fields=['account', 'symbol']),
            models.Index(fields=['account']),
        ]

    def __str__(self):
        return f"{self.account.user.username} - {self.symbol.ticker} ({self.quantity})"
    
    def save(self, *args, **kwargs):
        # 총 가치: 시세가 있을 때만 시장가 평가 (없으면 0)
        if self.quantity > 0 and self.current_price and self.current_price > 0:
            self.total_value = self.quantity * self.current_price
        else:
            self.total_value = Decimal('0')
        
        # 평가 손익: 시세와 평균매수가가 모두 있을 때만 계산
        if (
            self.quantity > 0
            and self.average_price
            and self.average_price > 0
            and self.current_price
            and self.current_price > 0
        ):
            cost = self.quantity * self.average_price
            value = self.quantity * self.current_price
            self.profit_loss = value - cost
            self.profit_rate = ((value - cost) / cost) * 100 if cost > 0 else Decimal('0')
        else:
            self.profit_loss = Decimal('0')
            self.profit_rate = Decimal('0')
        
        super().save(*args, **kwargs)


class TargetAllocationPlan(models.Model):
    """
    목표 비율 자동매매 계획.
    특정 종목이 총 자산(현물+예수금 등)에서 차지할 비율을 목표로,
    지정한 일수 동안 지정한 횟수만큼 나눠서 매수/매도하는 계획.
    """
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='target_allocation_plans',
        verbose_name='계좌'
    )
    symbol = models.ForeignKey(
        Symbol,
        on_delete=models.CASCADE,
        related_name='target_allocation_plans',
        verbose_name='종목'
    )
    target_ratio = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name='목표 비율 (0~1, 예: 0.2 = 20%)',
        help_text='총 자산 대비 이 종목이 차지할 목표 비율'
    )
    total_days = models.PositiveIntegerField(
        verbose_name='총 일수',
        help_text='몇 일에 걸쳐 매매할지'
    )
    num_trades = models.PositiveIntegerField(
        verbose_name='분할 횟수',
        help_text='몇 번에 나눠서 매매할지'
    )
    trades_done = models.PositiveIntegerField(
        default=0,
        verbose_name='완료된 매매 횟수'
    )
    start_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='시작일',
        help_text='비우면 저장 시 오늘로 설정됨'
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='종료일',
        help_text='비우면 start_date + total_days 로 설정됨'
    )
    enabled = models.BooleanField(
        default=True,
        verbose_name='활성 여부'
    )
    order_type = models.CharField(
        max_length=10,
        choices=OrderType.choices,
        default=OrderType.MARKET,
        verbose_name='주문 타입'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '목표 비율 자동매매 계획'
        verbose_name_plural = '목표 비율 자동매매 계획들'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'symbol'],
                name='unique_account_symbol_plan'
            ),
        ]

    def __str__(self):
        return f"{self.account} / {self.symbol.ticker} 목표 {float(self.target_ratio)*100:.1f}% ({self.trades_done}/{self.num_trades})"
    
    def save(self, *args, **kwargs):
        if self.start_date is None:
            self.start_date = timezone.now().date()
        if self.end_date is None and self.start_date and self.total_days:
            from datetime import timedelta
            self.end_date = self.start_date + timedelta(days=self.total_days)
        super().save(*args, **kwargs)


def generate_webhook_token():
    """Strategy webhook_token 기본값"""
    import secrets
    return secrets.token_urlsafe(32)


class StrategyVisibility(models.TextChoices):
    PUBLIC = 'PUBLIC', '공개'
    PRIVATE = 'PRIVATE', '비공개'


class AlertEventStatus(models.TextChoices):
    """TradingView 알림 이벤트 상태"""
    RECEIVED = 'RECEIVED', '수신'
    ACCEPTED = 'ACCEPTED', '수락'
    REJECTED = 'REJECTED', '거부'
    EXECUTING = 'EXECUTING', '실행중'
    COMPLETED = 'COMPLETED', '완료'
    FAILED = 'FAILED', '실패'


class AlertTradeStatus(models.TextChoices):
    """Alert 분할 매매 Plan/Leg 상태"""
    PENDING = 'PENDING', '대기'
    SCHEDULED = 'SCHEDULED', '예약'
    ORDERED = 'ORDERED', '주문생성'
    COMPLETED = 'COMPLETED', '완료'
    CANCELLED = 'CANCELLED', '취소'
    FAILED = 'FAILED', '실패'
    SKIPPED = 'SKIPPED', '스킵'


class Strategy(models.Model):
    """
    TradingView webhook 전략 템플릿.
    기본 비중/분할을 정의하고, 계좌는 StrategyLink로 연동한다.

    향후 수수료 확장 (미구현 — trading.strategy_fees 참고):
      - fee_enabled, fee_rate_bps 등 제작자 수익 필드
      - payee 는 보통 owner
      - PUBLIC 전략에만 과금하는 정책을 권장
    """
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='strategies',
        verbose_name='소유자'
    )
    title = models.CharField(max_length=100, verbose_name='제목')
    description = models.TextField(blank=True, default='', verbose_name='설명')
    visibility = models.CharField(
        max_length=10,
        choices=StrategyVisibility.choices,
        default=StrategyVisibility.PRIVATE,
        verbose_name='공개 범위'
    )
    webhook_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_webhook_token,
        verbose_name='웹훅 토큰',
        help_text='URL path에 사용되는 고유 시크릿'
    )
    webhook_passphrase = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        verbose_name='웹훅 패스프레이즈',
        help_text='payload secret과 일치해야 함 (비우면 미검증)'
    )
    default_max_position_weight_percent = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal('100'),
        validators=[MinValueValidator(Decimal('0.0001')), MaxValueValidator(Decimal('100'))],
        verbose_name='default 최대 포지션 비중 (%)',
        help_text='시드 대비 종목 최대 보유 비중 (매수 상한)'
    )
    default_trade_percent = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0.0001')), MaxValueValidator(Decimal('100'))],
        verbose_name='default 매매당 비중 (%)',
        help_text='시드 금액 대비 매수/매도 금액 비율'
    )
    default_split_count = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name='default 분할 횟수',
        help_text='1이면 일시 전량'
    )
    default_split_interval_seconds = models.PositiveIntegerField(
        default=0,
        verbose_name='default 분할 기간 (초)',
        help_text='분할 횟수가 1보다 클 때 Leg 간 대기 시간'
    )
    order_type = models.CharField(
        max_length=10,
        choices=OrderType.choices,
        default=OrderType.MARKET,
        verbose_name='주문 타입'
    )
    enabled = models.BooleanField(default=True, verbose_name='활성 여부')
    allowed_tickers = models.JSONField(
        blank=True,
        null=True,
        verbose_name='허용 티커 목록',
        help_text='null/빈 목록이면 브로커 Symbol만 있으면 OK'
    )
    cooldown_seconds = models.PositiveIntegerField(
        default=60,
        verbose_name='동일 티커 쿨다운 (초)',
        help_text='동일 전략·티커·액션 중복 알림 완화'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '전략'
        verbose_name_plural = '전략들'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.visibility})"


class StrategyLink(models.Model):
    """
    계좌 ↔ 전략 연동 (중계). override null이면 Strategy default 사용.

    향후 수수료 확장 (미구현 — trading.strategy_fees 참고):
      - fee_rate_bps override / 연동 시점 스냅샷
      - account.user == strategy.owner 이면 면제
      - 체결(FILLED) 시 StrategyCommission 원장 생성의 기준 엔티티
    """
    strategy = models.ForeignKey(
        Strategy,
        on_delete=models.CASCADE,
        related_name='links',
        verbose_name='전략'
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='strategy_links',
        verbose_name='계좌'
    )
    seed_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='고정 시드 금액'
    )
    seed_currency = models.CharField(
        max_length=10,
        choices=Currency.choices,
        default=Currency.KRW,
        verbose_name='시드 통화'
    )
    max_position_weight_percent = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal('0.0001')), MaxValueValidator(Decimal('100'))],
        verbose_name='최대 포지션 비중 override (%)',
    )
    trade_percent = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal('0.0001')), MaxValueValidator(Decimal('100'))],
        verbose_name='매매당 비중 override (%)',
    )
    split_count = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
        verbose_name='분할 횟수 override',
    )
    split_interval_seconds = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name='분할 기간 override (초)',
    )
    enabled = models.BooleanField(default=True, verbose_name='연동 활성')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '전략 연동'
        verbose_name_plural = '전략 연동들'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['strategy', 'account'],
                name='unique_strategy_account_link'
            ),
        ]

    def __str__(self):
        return f"{self.strategy.title} ↔ {self.account_id}"

    @property
    def effective_max_position_weight_percent(self) -> Decimal:
        if self.max_position_weight_percent is not None:
            return self.max_position_weight_percent
        return self.strategy.default_max_position_weight_percent

    @property
    def effective_trade_percent(self) -> Decimal:
        if self.trade_percent is not None:
            return self.trade_percent
        return self.strategy.default_trade_percent

    @property
    def effective_split_count(self) -> int:
        if self.split_count is not None:
            return int(self.split_count)
        return int(self.strategy.default_split_count)

    @property
    def effective_split_interval_seconds(self) -> int:
        if self.split_interval_seconds is not None:
            return int(self.split_interval_seconds)
        return int(self.strategy.default_split_interval_seconds)


class AlertEvent(models.Model):
    """TradingView webhook 수신 이벤트 (감사/재처리용)"""
    strategy = models.ForeignKey(
        Strategy,
        on_delete=models.CASCADE,
        related_name='events',
        verbose_name='전략'
    )
    raw_payload = models.JSONField(verbose_name='원본 페이로드')
    ticker = models.CharField(max_length=50, blank=True, default='', verbose_name='티커')
    action = models.CharField(
        max_length=10,
        choices=OrderSide.choices,
        blank=True,
        default='',
        verbose_name='액션'
    )
    idempotency_key = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name='멱등 키'
    )
    status = models.CharField(
        max_length=20,
        choices=AlertEventStatus.choices,
        default=AlertEventStatus.RECEIVED,
        verbose_name='상태'
    )
    reject_reason = models.TextField(blank=True, null=True, verbose_name='거부 사유')
    received_at = models.DateTimeField(auto_now_add=True, verbose_name='수신일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '알림 이벤트'
        verbose_name_plural = '알림 이벤트들'
        ordering = ['-received_at']
        constraints = [
            models.UniqueConstraint(
                fields=['strategy', 'idempotency_key'],
                condition=models.Q(idempotency_key__isnull=False),
                name='unique_alert_event_idempotency'
            ),
        ]

    def __str__(self):
        return f"{self.strategy.title} {self.action} {self.ticker} [{self.status}]"


class AlertTradePlan(models.Model):
    """알림 기반 분할 매매 계획 (연동 계좌당 1개)"""
    event = models.ForeignKey(
        AlertEvent,
        on_delete=models.CASCADE,
        related_name='trade_plans',
        verbose_name='이벤트'
    )
    strategy_link = models.ForeignKey(
        StrategyLink,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='trade_plans',
        verbose_name='전략 연동'
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='alert_trade_plans',
        verbose_name='계좌'
    )
    symbol = models.ForeignKey(
        Symbol,
        on_delete=models.CASCADE,
        related_name='alert_trade_plans',
        verbose_name='종목'
    )
    side = models.CharField(
        max_length=10,
        choices=OrderSide.choices,
        verbose_name='매수/매도'
    )
    total_notional = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='총 금액'
    )
    total_quantity = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        verbose_name='총 수량 (매도)'
    )
    reference_price = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        verbose_name='기준가'
    )
    split_count = models.PositiveIntegerField(verbose_name='분할 횟수')
    split_interval_seconds = models.PositiveIntegerField(default=0, verbose_name='분할 간격 (초)')
    order_type = models.CharField(
        max_length=10,
        choices=OrderType.choices,
        default=OrderType.MARKET,
        verbose_name='주문 타입'
    )
    status = models.CharField(
        max_length=20,
        choices=AlertTradeStatus.choices,
        default=AlertTradeStatus.PENDING,
        verbose_name='상태'
    )
    legs_done = models.PositiveIntegerField(default=0, verbose_name='완료된 Leg 수')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '알림 매매 계획'
        verbose_name_plural = '알림 매매 계획들'
        ordering = ['-created_at']

    def __str__(self):
        return f"Plan {self.id}: {self.side} {self.symbol.ticker} ({self.legs_done}/{self.split_count})"


class AlertTradeLeg(models.Model):
    """알림 분할 매매 개별 Leg"""
    plan = models.ForeignKey(
        AlertTradePlan,
        on_delete=models.CASCADE,
        related_name='legs',
        verbose_name='계획'
    )
    seq = models.PositiveIntegerField(verbose_name='순번')
    scheduled_at = models.DateTimeField(verbose_name='예약 시각')
    notional = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='금액'
    )
    quantity = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        verbose_name='수량'
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='alert_trade_legs',
        verbose_name='주문'
    )
    status = models.CharField(
        max_length=20,
        choices=AlertTradeStatus.choices,
        default=AlertTradeStatus.SCHEDULED,
        verbose_name='상태'
    )
    error_message = models.TextField(blank=True, null=True, verbose_name='오류 메시지')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '알림 매매 Leg'
        verbose_name_plural = '알림 매매 Legs'
        ordering = ['plan', 'seq']
        constraints = [
            models.UniqueConstraint(
                fields=['plan', 'seq'],
                name='unique_alert_trade_leg_seq'
            ),
        ]

    def __str__(self):
        return f"Leg {self.plan_id}-{self.seq} [{self.status}]"


class ExchangeFeeRebate(models.Model):
    """거래소 수수료 환급(페이백) 정책"""
    exchange_name = models.CharField(max_length=100, unique=True, verbose_name='거래소명')
    rebate_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='페이백률 (%)'
    )
    trading_discount_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='거래 할인율 (%)'
    )
    limit_order_fee_pct = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name='지정가 수수료율 (%)'
    )
    market_order_fee_pct = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name='시장가 수수료율 (%)'
    )
    avg_payback_krw = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='1인 평균 페이백 (원)'
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        verbose_name='태그',
        help_text='예: ["최상위 거래소", "신규 제휴"]'
    )
    source_url = models.URLField(max_length=500, blank=True, verbose_name='출처 URL')
    crawled_at = models.DateTimeField(null=True, blank=True, verbose_name='크롤링 시각')
    is_active = models.BooleanField(default=True, verbose_name='노출 여부')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '거래소 수수료 환급'
        verbose_name_plural = '거래소 수수료 환급'
        ordering = ['-rebate_pct', 'exchange_name']

    def __str__(self):
        return f"{self.exchange_name} (페이백 {self.rebate_pct}%)"


class PortfolioVisibility(models.TextChoices):
    PUBLIC = 'PUBLIC', '공개'
    PRIVATE = 'PRIVATE', '비공개'


class Portfolio(models.Model):
    """
    비중 미러링 포트폴리오. 여러 종목의 목표 비중(target_weight_percent)을 정의하고,
    PortfolioLink로 연동된 계좌는 비중 변경 시 즉시 리밸런싱된다.

    포트폴리오는 단일 통화로 구성한다 (holdings의 symbol.currency가 모두 동일해야 함).
    향후 수수료 확장 (미구현 — trading.strategy_fees 참고 패턴 재사용 예정):
      - 구독(PortfolioLink) 시 과금하려면 strategy_fees.py와 동일한 훅 방식을 따른다.
    """
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='portfolios',
        verbose_name='소유자'
    )
    title = models.CharField(max_length=100, verbose_name='제목')
    description = models.TextField(blank=True, default='', verbose_name='설명')
    visibility = models.CharField(
        max_length=10,
        choices=PortfolioVisibility.choices,
        default=PortfolioVisibility.PRIVATE,
        verbose_name='공개 범위'
    )
    order_type = models.CharField(
        max_length=10,
        choices=OrderType.choices,
        default=OrderType.MARKET,
        verbose_name='주문 타입'
    )
    enabled = models.BooleanField(default=True, verbose_name='활성 여부')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '포트폴리오'
        verbose_name_plural = '포트폴리오들'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.visibility})"


class PortfolioHolding(models.Model):
    """포트폴리오 구성 종목과 목표 비중"""
    portfolio = models.ForeignKey(
        Portfolio,
        on_delete=models.CASCADE,
        related_name='holdings',
        verbose_name='포트폴리오'
    )
    symbol = models.ForeignKey(
        Symbol,
        on_delete=models.CASCADE,
        related_name='portfolio_holdings',
        verbose_name='종목'
    )
    target_weight_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name='목표 비중 (%)',
        help_text='포트폴리오 시드 대비 이 종목의 목표 비중'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '포트폴리오 종목'
        verbose_name_plural = '포트폴리오 종목들'
        ordering = ['-target_weight_percent']
        constraints = [
            models.UniqueConstraint(
                fields=['portfolio', 'symbol'],
                name='unique_portfolio_symbol'
            ),
        ]

    def __str__(self):
        return f"{self.portfolio.title} / {self.symbol.ticker} {self.target_weight_percent}%"


class PortfolioLink(models.Model):
    """계좌 ↔ 포트폴리오 구독(연동). seed_amount 기준으로 목표 비중을 그대로 미러링한다."""
    portfolio = models.ForeignKey(
        Portfolio,
        on_delete=models.CASCADE,
        related_name='links',
        verbose_name='포트폴리오'
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='portfolio_links',
        verbose_name='계좌'
    )
    seed_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='고정 시드 금액'
    )
    seed_currency = models.CharField(
        max_length=10,
        choices=Currency.choices,
        default=Currency.KRW,
        verbose_name='시드 통화'
    )
    enabled = models.BooleanField(default=True, verbose_name='연동 활성')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '포트폴리오 연동'
        verbose_name_plural = '포트폴리오 연동들'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['portfolio', 'account'],
                name='unique_portfolio_account_link'
            ),
        ]

    def __str__(self):
        return f"{self.portfolio.title} ↔ {self.account_id}"
