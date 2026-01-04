# Generated manually

from django.db import migrations, models


def populate_broker_codes(apps, schema_editor):
    """기존 브로커 데이터에 code 값 설정"""
    Broker = apps.get_model('trading', 'Broker')
    
    # 기존 브로커들의 code 설정
    code_mapping = {
        'upbit': 'UPBIT',
        'bingx': 'BINGX',
        '한국투자증권': 'KIS',
        'kis': 'KIS',
    }
    
    for broker in Broker.objects.all():
        name_lower = broker.name.lower()
        # 이름 기반으로 code 설정
        code = None
        for key, value in code_mapping.items():
            if key in name_lower:
                code = value
                break
        
        # 매칭되지 않으면 이름을 대문자로 변환하여 사용
        if not code:
            code = broker.name.upper().replace(' ', '_')
        
        broker.code = code
        broker.save()


def reverse_populate_broker_codes(apps, schema_editor):
    """역방향 마이그레이션 (필요시)"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0004_dailyrealizedprofit'),
    ]

    operations = [
        migrations.AddField(
            model_name='broker',
            name='code',
            field=models.CharField(
                max_length=50,
                null=True,
                verbose_name='브로커 코드',
                help_text='시스템에서 사용하는 고유 식별자 (예: UPBIT, BINGX, KIS)'
            ),
        ),
        migrations.RunPython(populate_broker_codes, reverse_populate_broker_codes),
        migrations.AlterField(
            model_name='broker',
            name='code',
            field=models.CharField(
                max_length=50,
                unique=True,
                verbose_name='브로커 코드',
                help_text='시스템에서 사용하는 고유 식별자 (예: UPBIT, BINGX, KIS)'
            ),
        ),
    ]

