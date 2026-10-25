import logging
import signal
import sys
import threading
from tkinter import mainloop
from typing import Annotated, Literal

import httpx2 as httpx
from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s QBITCLEANER %(message)s"
)

GB = 1024**3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True, env_ignore_empty=True, env_prefix="qbit_cleaner_"
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    host: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    auth_key: SecretStr = SecretStr("")

    min_gb: Annotated[float, Field(ge=1)] = 1
    interval: int = 15
    dry_run: bool = True


def get_free_space(logger: logging.Logger, client: httpx.Client) -> int:
    logger.debug("Getting free space info...")
    return int(  # TODO: check return and calculate proper GB
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
):  # TODO add return type
    logger.debug("Getting torrents...")
    params = {"filter": "completed", "sort": "added_on"}
    return (
        client.request("GET", "/torrents/info", data=params).raise_for_status().json()
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
) -> None:
    free = get_free_space(logger, client)
    if free >= settings.min_gb:
        return

    candidates = get_torrents(logger, client, settings)
    if not candidates:
        logger.warning("No torrents eligible to delete... might run out of space :(")

    for t in candidates:
        logger.info(
            "%s %s (%.1f GB)",
            "would delete" if settings.dry_run else "deleting",
            t["name"],
            t["size"] / GB,
        )
        if settings.dry_run:
            logger.warning("Dry run enabled in settings - not deleting anything.")
            return


def main() -> int:
    logger = logging.getLogger("qbit-cleaner")

    logger.info("Starting...")
    stop = set_stop_on_signal(logger)

    logger.debug("Loading settings from enviorment...")
    settings = Settings()

    with httpx.Client(
        base_url=str(settings.host).rstrip("/" + "/api/v2"), timeout=10
    ) as client:
        logger.debug("Testing connection to qbittorrent instance...")
        logger.info(f"Running on {get_version(logger, client)}")
        while not stop.is_set():
            main_loop(logger, client, settings)
            stop.wait(settings.interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())
