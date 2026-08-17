"""헬스체크 엔드포인트 테스트"""
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class HealthCheckTestCase(TestCase):
    """/api/health/ 엔드포인트 테스트"""

    def setUp(self):
        self.client = APIClient()

    def test_health_check_ok_without_authentication(self):
        """인증 없이 호출 가능하며 DB가 정상이면 200과 status=ok를 반환한다"""
        response = self.client.get(reverse('health-check'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ok')
        self.assertEqual(response.data['checks']['database'], 'ok')
        self.assertIn('timestamp', response.data)
