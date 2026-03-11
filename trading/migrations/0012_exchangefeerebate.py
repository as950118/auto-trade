# Generated for ExchangeFeeRebate (fee rebate comparison)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0011_target_allocation_plan'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExchangeFeeRebate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('exchange_name', models.CharField(max_length=100, verbose_name='거래소명')),
                ('rebate_pct', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True, verbose_name='페이백률 (%)')),
                ('trading_discount_pct', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True, verbose_name='거래 할인율 (%)')),
                ('limit_order_fee_pct', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='지정가 수수료율 (%)')),
                ('market_order_fee_pct', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True, verbose_name='시장가 수수료율 (%)')),
                ('avg_payback_krw', models.IntegerField(blank=True, null=True, verbose_name='1인 평균 페이백 (원)')),
                ('tags', models.JSONField(blank=True, default=list, help_text='예: ["최상위 거래소", "신규 제휴"]', verbose_name='태그')),
                ('source_url', models.URLField(blank=True, max_length=500, verbose_name='출처 URL')),
                ('crawled_at', models.DateTimeField(blank=True, null=True, verbose_name='크롤링 시각')),
                ('is_active', models.BooleanField(default=True, verbose_name='노출 여부')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='생성일시')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='수정일시')),
            ],
            options={
                'verbose_name': '거래소 수수료 환급',
                'verbose_name_plural': '거래소 수수료 환급',
                'ordering': ['-rebate_pct', 'exchange_name'],
            },
        ),
    ]
