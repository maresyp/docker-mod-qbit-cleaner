# /// script
# requires-python = ">=3.14,<3.15"
# dependencies = ["httpx2>=2.15,<3", "pydantic>=2.10", "pydantic-settings>=2.7,<3"]
# ///
import logging
import signal
import sys
import threading
from typing import Annotated, Literal

import httpx2 as httpx
from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s QBitCleaner %(message)s"
)

logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpcore2").setLevel(logging.WARNING)

GB = 1024**3
SETTLE_SECONDS = 10


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True, env_ignore_empty=True, env_prefix="qbit_cleaner_"
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    host: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    auth_key: SecretStr = SecretStr("")

    min_gb: Annotated[float, Field(ge=1)] = 1
    interval: int = 15
    dry_run: bool = False


def make_client(settings: Settings, **kwargs) -> httpx.Client:
    return httpx.Client(
        base_url=str(settings.host).rstrip("/") + "/api/v2", timeout=10, **kwargs
    )


def get_free_space(logger: logging.Logger, client: httpx.Client) -> int:
    logger.debug("Getting free space info...")
    return int(
        client.request("GET", "/sync/maindata")
        .raise_for_status()
        .json()["server_state"]["free_space_on_disk"]
    )


def get_version(logger: logging.Logger, client: httpx.Client) -> str:
    logger.debug("Getting qbittorrent version...")
    return client.request("GET", "/app/version").raise_for_status().text


def get_torrents(
    logger: logging.Logger,
    client: httpx.Client,
    settings: Settings,
) -> list[dict]:
    logger.debug("Getting torrents...")
    params = {"filter": "completed", "sort": "added_on"}
    return (
        client.request("GET", "/torrents/info", params=params).raise_for_status().json()
    )


def delete_torrent(
    logger: logging.Logger,
    client: httpx.Client,
    settings: Settings,
    hash: str,
):
    logger.debug(f"Deleting torrent with {hash=}")
    params = {"deleteFiles": "true", "hashes": hash}
    client.request("POST", "/torrents/delete", data=params).raise_for_status()


def set_stop_on_signal(logger: logging.Logger) -> threading.Event:
    """Return an Event that gets set when SIGTERM or SIGINT arrives."""
    stop = threading.Event()
    signals = {signal.SIGTERM, signal.SIGINT}
    signal.pthread_sigmask(signal.SIG_BLOCK, signals)  # inherited below

    def watch() -> None:
        name = signal.Signals(signal.sigwait(signals)).name
        logger.info("received %s, shutting down", name)
        stop.set()

    threading.Thread(target=watch, daemon=True).start()
    return stop


def main_loop(
    logger: logging.Logger,
    client: httpx.Client,
    settings: Settings,
    stop: threading.Event,
) -> None:
    minimum = settings.min_gb * GB
    free = get_free_space(logger, client)
    if free >= minimum:
        return
    logger.warning(
        "low disk space: %.1f GB free, minimum is %.1f GB", free / GB, settings.min_gb
    )

    candidates = get_torrents(logger, client, settings)
    if not candidates:
        logger.warning("No torrents eligible to delete... might run out of space :(")
        return

    for t in candidates:
        if free >= minimum or stop.is_set():
            break
        logger.info(
            "%s %s (%.1f GB)",
            "would delete" if settings.dry_run else "deleting",
            t["name"],
            t["size"] / GB,
        )
        if settings.dry_run:
            free += t["size"]  # estimate only
            continue
        delete_torrent(logger, client, settings, t["hash"])
        if stop.wait(SETTLE_SECONDS):
            break
        free = get_free_space(logger, client)

    if settings.dry_run:
        logger.warning("Dry run enabled in settings - not deleting anything.")


def main() -> int:
    logger = logging.getLogger("qbit-cleaner")

    logger.debug("Starting...")
    stop = set_stop_on_signal(logger)

    logger.debug("Loading settings from enviorment...")
    settings = Settings()
    logging.getLogger().setLevel(settings.log_level)

    with make_client(settings) as client:
        logger.debug("Testing connection to qbittorrent instance...")
        logger.info(f"connected to qbittorrent {get_version(logger, client)}")
        while not stop.is_set():
            main_loop(logger, client, settings, stop)
            stop.wait(settings.interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())
