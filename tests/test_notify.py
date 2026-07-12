from dataclasses import replace

import psylocke.notify as notify


def test_send_is_a_noop_without_credentials(config, monkeypatch):
    calls = []
    monkeypatch.setattr("requests.post", lambda *a, **kw: calls.append((a, kw)))

    notify.send(config, "hello")

    assert calls == []


def test_send_posts_to_telegram_when_configured(config, monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)
    configured = replace(config, telegram_bot_token="TOKEN", telegram_chat_id="123")

    notify.send(configured, "hello")

    assert len(calls) == 1
    url, payload, _timeout = calls[0]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert payload["chat_id"] == "123"
    assert payload["text"] == "hello"


def test_send_swallows_request_errors(config, monkeypatch):
    def fake_post(*a, **kw):
        raise ConnectionError("network down")

    monkeypatch.setattr("requests.post", fake_post)
    configured = replace(config, telegram_bot_token="TOKEN", telegram_chat_id="123")

    notify.send(configured, "hello")  # must not raise
