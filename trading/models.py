from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
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
    ticker = models.CharField(max_length=50, unique=True, verbose_name='티커')
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='생성일시')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='수정일시')

    class Meta:
        verbose_name = '종목'
        verbose_name_plural = '종목들'
        ordering = ['ticker']

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
    account_number = models.CharField(max_length=100, verbose_name='계좌번호')
    account_password = models.CharField(max_length=255, verbose_name='계좌비밀번호')
    api_key = models.CharField(max_length=255, blank=True, null=True, verbose_name='API 키')
    api_secret = models.CharField(max_length=255, blank=True, null=True, verbose_name='API 시크릿')
    
    # 자산 정보
    total_assets = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='총 자산 (예수금+보유종목현재가치)'
    )
    cash_balance = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='예수금'
    )
    stock_value = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name='보유종목현재가치'
    )
    profit_rate = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
        verbose_name='수익률 (%)'
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
        unique_together = ['user', 'broker', 'account_number']

    def __str__(self):
        return f"{self.user.username} - {self.broker.name} ({self.account_number})"


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
