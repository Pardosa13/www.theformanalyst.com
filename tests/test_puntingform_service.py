from unittest.mock import patch

from puntingform_service import PuntingFormService


class FakeResponse:
    status_code = 200
    ok = True
    headers = {'content-type': 'text/plain; charset=utf-8'}
    text = (
        'StartDate,EntityId,EntityName,CareerWins,Last100Wins\n'
        '2026-07-05,123,Example Trainer,10,4\n'
    )


def test_fetch_v2_strike_rate_rows_accepts_text_plain_with_expected_csv_header(monkeypatch):
    monkeypatch.setenv('PUNTINGFORM_API_KEY', 'test-key')

    class FakeSession:
        def send(self, request, timeout):
            return FakeResponse()

    with patch('puntingform_service.requests.Session', return_value=FakeSession()):
        rows, headers = PuntingFormService()._fetch_v2_strike_rate_rows('trainer', jurisdiction=2)

    assert headers[:3] == ['StartDate', 'EntityId', 'EntityName']
    assert rows == [
        {
            'StartDate': '2026-07-05',
            'EntityId': '123',
            'EntityName': 'Example Trainer',
            'CareerWins': '10',
            'Last100Wins': '4',
        }
    ]
