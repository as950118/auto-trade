"""
TradingView webhook 수신 엔드포인트 (인증 없음, 토큰으로 라우팅).
"""
import json
import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .alert_guards import AlertGuardError
from .services.alert_strategy import AlertStrategyService

logger = logging.getLogger(__name__)


@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def tradingview_webhook(request, webhook_token: str):
    """
    TradingView Alert webhook.
    POST /api/webhooks/tradingview/<webhook_token>/
    Body JSON: { "ticker": "...", "action": "BUY"|"SELL", "secret": "...", "alert_id": "..." }
    """
    payload = request.data
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return Response(
                {'detail': 'Invalid JSON payload'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    if not isinstance(payload, dict):
        # TradingView sometimes sends form body; try to coerce
        try:
            payload = dict(payload)
        except Exception:
            return Response(
                {'detail': 'Payload must be a JSON object'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        event = AlertStrategyService.handle_webhook(webhook_token, payload)
    except AlertGuardError as exc:
        if exc.code == 'STRATEGY_NOT_FOUND':
            return Response({'detail': exc.message}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {'detail': exc.message, 'code': exc.code},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        logger.exception('Webhook handling error')
        return Response(
            {'detail': 'Internal error'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            'event_id': event.id,
            'status': event.status,
            'ticker': event.ticker,
            'action': event.action,
            'reject_reason': event.reject_reason,
        },
        status=status.HTTP_200_OK,
    )
