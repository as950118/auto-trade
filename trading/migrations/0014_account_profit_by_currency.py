# Generated manually for currency-separated account P&L

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0013_exchangefeerebate_exchange_name_unique'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='profit_loss',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=20, verbose_name='평가손익'),
        ),
        migrations.AddField(
            model_name='account',
            name='profit_loss_krw',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=20, verbose_name='평가손익 (KRW)'),
        ),
        migrations.AddField(
            model_name='account',
            name='profit_loss_usd',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=20, verbose_name='평가손익 (USD)'),
        ),
        migrations.AddField(
            model_name='account',
            name='profit_rate_krw',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=10, verbose_name='수익률 (KRW, %)'),
        ),
        migrations.AddField(
            model_name='account',
            name='profit_rate_usd',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=10, verbose_name='수익률 (USD, %)'),
        ),
    ]
