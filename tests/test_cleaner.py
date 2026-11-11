import logging
import threading
from urllib.parse import parse_qs

import cleaner
import httpx2 as httpx
import pytest
from cleaner import GB, Settings, main_loop, make_client
from pydantic import ValidationError

log = logging.getLogger("test")


@pytest.fixture(autouse=True)
def no_settle_delay(monkeypatch):
    monkeypatch.setattr(cleaner, "SETTLE_SECONDS", 0)


class FakeQbittorrent:
    """In-memory stand-in for the WebUI API, used as an httpx MockTransport handler.

    It enforces the real HTTP contract (query string for GETs, form body for POSTs),
    so a request that qBittorrent would misread fails here too.
    """

    def __init__(self, free_gb, torrents=()):
        self.free = free_gb * GB
        self.torrents = list(torrents)
        self.deleted = []
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v2/sync/maindata":
            return httpx.Response(
                200, json={"server_state": {"free_space_on_disk": self.free}}
            )
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.0.0")
        if path == "/api/v2/torrents/info":
            query = request.url.params
            assert query.get("filter") == "completed", (
                "filter must be sent as a query parameter"
            )
            torrents = list(self.torrents)
            if sort := query.get("sort"):
                torrents.sort(key=lambda t: t[sort])
            return httpx.Response(200, json=torrents)
        if path == "/api/v2/torrents/delete":
            assert request.method == "POST"
            form = parse_qs(request.content.decode())
            assert form["deleteFiles"] == ["true"], (
                "data must be deleted with the torrent"
            )
            for torrent_hash in form["hashes"][0].split("|"):
                torrent = next(t for t in self.torrents if t["hash"] == torrent_hash)
                self.torrents.remove(torrent)
                self.free += torrent["size"]
                self.deleted.append(torrent_hash)
            return httpx.Response(200)
        return httpx.Response(404)

    def paths(self):
        return [r.url.path for r in self.requests]


def torrent(name, size_gb=30, added_on=0):
    return {"hash": name, "name": name, "size": size_gb * GB, "added_on": added_on}


def sweep(fake, stop=None, **overrides):
    settings = Settings(**{"min_gb": 50, "dry_run": False, **overrides})
    with make_client(settings, transport=httpx.MockTransport(fake)) as client:
        main_loop(log, client, settings, stop or threading.Event())


def test_does_nothing_when_enough_space():
    fake = FakeQbittorrent(free_gb=80, torrents=[torrent("a")])
    sweep(fake)
    assert fake.deleted == []
    assert "/api/v2/torrents/info" not in fake.paths()  # didn't even list torrents


def test_deletes_oldest_first_until_minimum_is_free():
    fake = FakeQbittorrent(
        free_gb=40,
        torrents=[
            torrent("newest", added_on=3),
            torrent("oldest", added_on=1),
            torrent("middle", added_on=2),
        ],
    )
    sweep(fake, min_gb=90)
    assert fake.deleted == ["oldest", "middle"]  # 40 -> 70 -> 100 GB free, then stop
    assert [t["hash"] for t in fake.torrents] == ["newest"]


def test_lists_only_completed_torrents_sorted_by_age():
    fake = FakeQbittorrent(free_gb=10, torrents=[torrent("a")])
    sweep(fake)
    listing = next(r for r in fake.requests if r.url.path == "/api/v2/torrents/info")
    assert dict(listing.url.params) == {"filter": "completed", "sort": "added_on"}


def test_dry_run_deletes_nothing_but_shows_the_plan(caplog):
    caplog.set_level(logging.INFO)
    fake = FakeQbittorrent(
        free_gb=10, torrents=[torrent("a"), torrent("b", added_on=1)]
    )
    sweep(fake, dry_run=True, min_gb=60)
    assert fake.deleted == []
    assert "/api/v2/torrents/delete" not in fake.paths()
    assert caplog.text.count("would delete") == 2  # full plan: 10 -> 40 -> 70 GB


def test_stop_requested_prevents_deletes():
    fake = FakeQbittorrent(free_gb=10, torrents=[torrent("a"), torrent("b")])
    stop = threading.Event()
    stop.set()
    sweep(fake, stop=stop)
    assert fake.deleted == []


def test_stop_requested_during_settle_ends_the_sweep():
    fake = FakeQbittorrent(
        free_gb=10, torrents=[torrent("a", added_on=1), torrent("b", added_on=2)]
    )
    stop = threading.Event()

    def stop_after_first_delete(request):
        response = fake(request)
        if request.url.path == "/api/v2/torrents/delete":
            stop.set()  # as if SIGTERM arrived while the delete was in flight
        return response

    settings = Settings(min_gb=500, dry_run=False)
    with make_client(
        settings, transport=httpx.MockTransport(stop_after_first_delete)
    ) as client:
        main_loop(log, client, settings, stop)
    assert fake.deleted == ["a"]  # the in-flight delete completed, nothing after it


def test_warns_when_nothing_is_eligible(caplog):
    caplog.set_level(logging.INFO)
    fake = FakeQbittorrent(free_gb=10, torrents=[])
    sweep(fake)
    assert "No torrents eligible" in caplog.text


def test_api_error_propagates_so_the_service_restarts():
    def broken(request):
        return httpx.Response(500)

    settings = Settings(min_gb=50)
    with make_client(settings, transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            main_loop(log, client, settings, threading.Event())


@pytest.mark.parametrize(
    "host", ["http://localhost:8080", "http://localhost:8080/", "http://qbit-api"]
)
def test_client_base_url_always_ends_in_api_v2(host):
    client = make_client(Settings(host=host))
    assert str(client.base_url).rstrip("/") == host.rstrip("/") + "/api/v2"


def test_settings_defaults_are_safe():
    settings = Settings()
    assert settings.dry_run is True
    assert settings.min_gb == 1


def test_settings_read_prefixed_env(monkeypatch):
    monkeypatch.setenv("QBIT_CLEANER_MIN_GB", "75")
    monkeypatch.setenv("QBIT_CLEANER_DRY_RUN", "false")
    monkeypatch.setenv("QBIT_CLEANER_INTERVAL", "")  # empty -> default
    settings = Settings()
    assert (settings.min_gb, settings.dry_run, settings.interval) == (75, False, 15)


def test_settings_reject_nonsense(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(min_gb=0)
    monkeypatch.setenv("QBIT_CLEANER_LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings()
