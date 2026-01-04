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
    is_delisted = models.BooleanField(default=False, verbose_name='상장폐지 여부')
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
    
    @property
    def stock_value(self):
        """
        보유종목현재가치 (통합 - 원화 기준)
        원화 보유종목가치 + (달러 보유종목가치 * 환율)
        환율은 현재 시점의 환율을 사용해야 하지만, 간단히 원화 변환된 값을 사용
        """
        # stock_value_krw는 이미 해외 주식이 원화로 변환된 값이 포함되어 있음
        # 따라서 stock_value_krw를 그대로 반환
        return self.stock_value_krw
    
    @property
    def cash_balance(self):
        """
        예수금 (통합 - 원화 기준)
        원화 예수금 + (달러 예수금 * 환율)
        """
        # 현재는 원화 예수금만 반환 (달러 예수금은 보통 없음)
        # 필요시 환율 적용하여 계산 가능
        return self.cash_balance_krw
    
    @property
    def total_assets(self):
        """
        총 자산 (통합 - 원화 기준)
        원화 총 자산 + (달러 총 자산 * 환율)
        """
        # total_assets_krw는 이미 해외 주식이 원화로 변환된 값이 포함되어 있음
        return self.total_assets_krw


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
        # 총 가치 계산
        if self.quantity > 0:
            if self.current_price > 0:
                self.total_value = self.quantity * self.current_price
            elif self.average_price > 0:
                # 현재가가 없으면 평균 매수가로 계산 (임시)
                self.total_value = self.quantity * self.average_price
        
        # 평가 손익 계산 (통화 구분 없이 계산 - 같은 통화 기준)
        if self.quantity > 0 and self.average_price > 0:
            cost = self.quantity * self.average_price
            
            # 현재가가 있으면 현재가 기준, 없으면 평균 매수가 기준
            if self.current_price > 0:
                value = self.quantity * self.current_price
            else:
                # 현재가가 없으면 평균 매수가를 현재가로 사용
                value = cost
            
            # 평가 손익 계산
            self.profit_loss = value - cost
            
            # 수익률 계산
            if cost > 0:
                self.profit_rate = ((value - cost) / cost) * 100
            else:
                self.profit_rate = Decimal('0')
        else:
            # 수량이 0이면 초기화
            self.profit_loss = Decimal('0')
            self.profit_rate = Decimal('0')
        
        super().save(*args, **kwargs)
