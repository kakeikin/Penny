# test/lambda/test_push_notification.py
import json
import pytest
from unittest.mock import patch, MagicMock, call
import sys, os

_push_notification_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../lambda/push-notification')
)
if _push_notification_path not in sys.path:
    sys.path.insert(0, _push_notification_path)


@pytest.fixture(autouse=True)
def reload_push_notification_index(monkeypatch):
    """Isolate module state between tests.
    VAPID_CLAIMS_EMAIL must be set before import because index.py validates it at module level.
    """
    monkeypatch.setenv('PUSH_SUBSCRIPTIONS_TABLE', 'finance-push-subscriptions')
    monkeypatch.setenv('VAPID_CLAIMS_EMAIL', 'mailto:test@example.com')

    saved_path = sys.path[:]
    saved_module = sys.modules.get('index')

    sys.modules.pop('index', None)
    if _push_notification_path in sys.path:
        sys.path.remove(_push_notification_path)
    sys.path.insert(0, _push_notification_path)

    yield

    sys.modules.pop('index', None)
    sys.path[:] = saved_path
    if saved_module is not None:
        sys.modules['index'] = saved_module


def make_sns_event(alerts, year_month='2026-03'):
    return {
        'Records': [{
            'Sns': {
                'Message': json.dumps({
                    'yearMonth': year_month,
                    'alerts': alerts,
                })
            }
        }]
    }


def make_subscription(endpoint='https://push.example.com/abc'):
    return json.dumps({
        'endpoint': endpoint,
        'keys': {'p256dh': 'key1', 'auth': 'auth1'}
    })


@patch('index.webpush')
@patch('index.dynamodb')
@patch('index.get_vapid_keys')
def test_sends_push_to_all_subscriptions(mock_vapid, mock_dynamo, mock_webpush):
    """webpush is called with correct subscription_info and vapid_private_key."""
    mock_vapid.return_value = ('pub_key', 'priv_key')
    sub_table = MagicMock()
    mock_dynamo.Table.return_value = sub_table
    sub_json = make_subscription()
    sub_table.scan.return_value = {'Items': [
        {'endpoint': 'https://push.example.com/abc', 'subscription': sub_json},
    ]}

    import index
    index.handler(make_sns_event([{'accountId': '5100', 'spent': 150, 'limit': 100, 'overage': 50}]), {})

    mock_webpush.assert_called_once()
    kwargs = mock_webpush.call_args[1]
    assert kwargs['subscription_info'] == json.loads(sub_json)
    assert kwargs['vapid_private_key'] == 'priv_key'
    assert kwargs['vapid_claims'] == {'sub': 'mailto:test@example.com'}


@patch('index.webpush')
@patch('index.dynamodb')
@patch('index.get_vapid_keys')
def test_skips_on_empty_subscriptions(mock_vapid, mock_dynamo, mock_webpush):
    mock_vapid.return_value = ('pub_key', 'priv_key')
    sub_table = MagicMock()
    mock_dynamo.Table.return_value = sub_table
    sub_table.scan.return_value = {'Items': []}

    import index
    index.handler(make_sns_event([{'accountId': '5100', 'spent': 150, 'limit': 100, 'overage': 50}]), {})

    mock_webpush.assert_not_called()


@patch('index.webpush')
@patch('index.dynamodb')
@patch('index.get_vapid_keys')
def test_removes_expired_subscription_on_410(mock_vapid, mock_dynamo, mock_webpush):
    """A 410 Gone response means the subscription expired — delete it from DynamoDB."""
    from pywebpush import WebPushException

    mock_vapid.return_value = ('pub_key', 'priv_key')
    sub_table = MagicMock()
    mock_dynamo.Table.return_value = sub_table

    expired_endpoint = 'https://push.example.com/expired'
    sub_table.scan.return_value = {'Items': [
        {'endpoint': expired_endpoint, 'subscription': make_subscription(expired_endpoint)},
    ]}

    mock_response = MagicMock()
    mock_response.status_code = 410
    mock_webpush.side_effect = WebPushException("Gone", response=mock_response)

    import index
    index.handler(make_sns_event([{'accountId': '5100', 'spent': 150, 'limit': 100, 'overage': 50}]), {})

    sub_table.delete_item.assert_called_once_with(Key={'endpoint': expired_endpoint})


@patch('index.webpush')
@patch('index.dynamodb')
@patch('index.get_vapid_keys')
def test_removes_expired_subscription_on_404(mock_vapid, mock_dynamo, mock_webpush):
    """A 404 Not Found response also means subscription is gone — delete it."""
    from pywebpush import WebPushException

    mock_vapid.return_value = ('pub_key', 'priv_key')
    sub_table = MagicMock()
    mock_dynamo.Table.return_value = sub_table

    expired_endpoint = 'https://push.example.com/notfound'
    sub_table.scan.return_value = {'Items': [
        {'endpoint': expired_endpoint, 'subscription': make_subscription(expired_endpoint)},
    ]}

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_webpush.side_effect = WebPushException("Not Found", response=mock_response)

    import index
    index.handler(make_sns_event([{'accountId': '5100', 'spent': 150, 'limit': 100, 'overage': 50}]), {})

    sub_table.delete_item.assert_called_once_with(Key={'endpoint': expired_endpoint})
