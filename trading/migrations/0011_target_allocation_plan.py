# Generated manually for TargetAllocationPlan

from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0010_alter_account_unique_together_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='TargetAllocationPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_ratio', models.DecimalField(decimal_places=4, help_text='총 자산 대비 이 종목이 차지할 목표 비율', max_digits=6, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1)], verbose_name='목표 비율 (0~1, 예: 0.2 = 20%)')),
                ('total_days', models.PositiveIntegerField(help_text='몇 일에 걸쳐 매매할지', verbose_name='총 일수')),
                ('num_trades', models.PositiveIntegerField(help_text='몇 번에 나눠서 매매할지', verbose_name='분할 횟수')),
                ('trades_done', models.PositiveIntegerField(default=0, verbose_name='완료된 매매 횟수')),
                ('start_date', models.DateField(blank=True, help_text='비우면 저장 시 오늘로 설정됨', null=True, verbose_name='시작일')),
                ('end_date', models.DateField(blank=True, help_text='비우면 start_date + total_days 로 설정됨', null=True, verbose_name='종료일')),
                ('enabled', models.BooleanField(default=True, verbose_name='활성 여부')),
                ('order_type', models.CharField(choices=[('MARKET', '시장가'), ('LIMIT', '지정가')], default='MARKET', max_length=10, verbose_name='주문 타입')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='생성일시')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='수정일시')),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='target_allocation_plans', to='trading.account', verbose_name='계좌')),
                ('symbol', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='target_allocation_plans', to='trading.symbol', verbose_name='종목')),
            ],
            options={
                'verbose_name': '목표 비율 자동매매 계획',
                'verbose_name_plural': '목표 비율 자동매매 계획들',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='targetallocationplan',
            constraint=models.UniqueConstraint(fields=('account', 'symbol'), name='unique_account_symbol_plan'),
        ),
    ]
