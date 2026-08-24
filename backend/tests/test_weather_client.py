"""What `refresh_weather` does when Open-Meteo does not answer with weather.

The startup path awaits this function, so its contract - return False, never
raise - is load-bearing in a way the scheduled path's is not: an exception here
does not lose the weather, it loses the whole app. That is the bug these tests
exist for. Every failure mode below answered 200, which is what makes it
interesting: `raise_for_status` is satisfied and the decode is what fails.
"""

import httpx
import pytest

from app.config import Settings
from app.weather import client as weather_client


class FakeResponse:
    def __init__(self, status: int, payload=None, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        if self._payload is None:
            # What httpx actually raises for a body that is not JSON.
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


FORECAST = {
    "current": {"temperature_2m": 21.5},
    "current_units": {"temperature_2m": "°C"},
    "daily": {"sunrise": ["2026-08-24T06:12"]},
    "daily_units": {},
    "hourly": {"temperature_2m": [21.0]},
    "hourly_units": {},
}
AIR_QUALITY = {"current": {"us_aqi": 34}}


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """A real Settings, with the developer's own .env kept out of it."""
    monkeypatch.setattr(
        weather_client,
        "settings",
        Settings(_env_file=None, weather_latitude=51.5, weather_longitude=-0.12),
    )


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    """The cache is a module global, so it has to be reset per test or the
    first success would make every later failure look like it worked."""
    monkeypatch.setattr(weather_client, "_cache", None)


def serve(monkeypatch, forecast, air_quality=None):
    """Answer the two calls refresh_weather makes, in order."""
    responses = [forecast, air_quality if air_quality is not None else FakeResponse(200, AIR_QUALITY)]
    calls = iter(responses)
    monkeypatch.setattr("app.weather.client.httpx.get", lambda *a, **k: next(calls))


class TestAGoodResponse:
    def test_it_fills_the_cache(self, monkeypatch):
        serve(monkeypatch, FakeResponse(200, FORECAST))

        assert weather_client.refresh_weather() is True

        cached = weather_client.get_cached_weather()
        assert cached["current"]["temperature_2m"] == 21.5
        assert cached["air_quality"]["us_aqi"] == 34
        assert "fetched_at" in cached

    def test_open_meteos_own_unit_labels_are_passed_through(self, monkeypatch):
        """Deriving the degree label from the setting instead would let the
        numbers and the label disagree after a unit change."""
        serve(monkeypatch, FakeResponse(200, FORECAST))
        weather_client.refresh_weather()

        assert weather_client.get_cached_weather()["current_units"]["temperature_2m"] == "°C"


class TestABodyThatIsNotJson:
    def test_html_with_a_200_returns_false_rather_than_raising(self, monkeypatch):
        """The regression this file was written for. A captive portal, a
        transparent proxy and a maintenance page all answer 200 with HTML, so
        raise_for_status passes and .json() raises a ValueError - which is not
        an httpx.HTTPError, so it used to escape. Startup awaits this call, so
        escaping meant the container never came up: no calendar, no photos and
        no bedtime schedule, over the weather widget."""
        serve(monkeypatch, FakeResponse(200, None, text="<html>Sign in to wifi</html>"))

        assert weather_client.refresh_weather() is False

    def test_the_air_quality_call_is_covered_too(self, monkeypatch):
        """The second call is as exposed as the first, and a half-filled cache
        would be worse than none."""
        serve(monkeypatch, FakeResponse(200, FORECAST), FakeResponse(200, None))

        assert weather_client.refresh_weather() is False
        assert weather_client.get_cached_weather() is None


class TestOrdinaryFailures:
    def test_a_connection_error_returns_false(self, monkeypatch):
        def boom(*args, **kwargs):
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr("app.weather.client.httpx.get", boom)

        assert weather_client.refresh_weather() is False

    def test_a_500_returns_false(self, monkeypatch):
        serve(monkeypatch, FakeResponse(500))

        assert weather_client.refresh_weather() is False


class TestTheCacheSurvivesAFailure:
    def test_a_later_failure_does_not_wipe_good_weather(self, monkeypatch):
        """A stale forecast on the wall beats a blank one. The cache is only
        ever replaced wholesale on success, never cleared on the way in."""
        serve(monkeypatch, FakeResponse(200, FORECAST))
        weather_client.refresh_weather()

        serve(monkeypatch, FakeResponse(200, None))
        assert weather_client.refresh_weather() is False

        assert weather_client.get_cached_weather()["current"]["temperature_2m"] == 21.5
