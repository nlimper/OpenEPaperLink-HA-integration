"""Tests for retrying an image upload to the AP when it answers 409 ("come back later")."""
import sys
from unittest.mock import MagicMock

import pytest
from PIL import Image

sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()
sys.modules['homeassistant.components.bluetooth'] = MagicMock()
from homeassistant.exceptions import HomeAssistantError
from custom_components.open_epaper_link import upload


class FakeAP:
    """Stands in for requests.post: answers /imgupload with the given (status, headers) in turn, then 200."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = 0

    def post(self, url, headers=None, data=None, timeout=None):
        self.requests += 1
        status, response_headers = self.responses.pop(0) if self.responses else (200, {})
        response = MagicMock()
        response.status_code = status
        response.headers = response_headers
        response.text = "Ok, saved" if status == 200 else "Busy, come back later"
        return response


@pytest.fixture
def waits(monkeypatch):
    """Record the retry waits instead of sleeping."""
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(upload.asyncio, "sleep", fake_sleep)
    return recorded


async def do_upload(monkeypatch, ap):
    monkeypatch.setattr(upload.requests, "post", ap.post)

    async def run_job(func, *args):
        return func(*args)

    hub = MagicMock()
    hub.host = "192.0.2.1"
    hub.hass.async_add_executor_job = run_job
    await upload.upload_to_hub(hub, "open_epaper_link.0000021ee3963b16", Image.new("RGB", (16, 16)), 0, 60)


async def test_retries_after_409_with_retry_after(monkeypatch, waits):
    ap = FakeAP([(409, {"Retry-After": "3"})])
    await do_upload(monkeypatch, ap)
    assert ap.requests == 2
    assert waits == [3]


async def test_409_without_retry_after_waits_the_default(monkeypatch, waits):
    ap = FakeAP([(409, {})])
    await do_upload(monkeypatch, ap)
    assert ap.requests == 2
    assert waits == [upload.BUSY_DEFAULT_WAIT]


async def test_gives_up_after_waiting_the_maximum(monkeypatch, waits):
    ap = FakeAP([(409, {"Retry-After": "30"})] * 10)
    with pytest.raises(HomeAssistantError) as err:
        await do_upload(monkeypatch, ap)
    assert waits == [30, 30]
    assert err.value.translation_key == "image_upload_status"


async def test_other_errors_are_not_retried(monkeypatch, waits):
    ap = FakeAP([(400, {})])
    with pytest.raises(HomeAssistantError) as err:
        await do_upload(monkeypatch, ap)
    assert ap.requests == 1
    assert waits == []
    assert err.value.translation_key == "image_upload_status"
