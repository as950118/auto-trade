# Add unique constraint on exchange_name for ExchangeFeeRebate

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0012_exchangefeerebate'),
    ]

    operations = [
        migrations.AlterField(
            model_name='exchangefeerebate',
            name='exchange_name',
            field=models.CharField(max_length=100, unique=True, verbose_name='거래소명'),
        ),
    ]
