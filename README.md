# docker-mod-qbit-cleaner

Automatically frees disk space by removing completed torrents from qBittorrent when available disk space falls below a configured minimum.

The cleaner periodically checks available disk space and deletes completed torrents until the configured minimum is reached.

## Configuration

All options use the `QBIT_CLEANER_` environment variable prefix.

| Variable | Default | Description |
|---|---|---|
| `QBIT_CLEANER_HOST` | `http://localhost:8080` | qBittorrent WebUI URL |
| `QBIT_CLEANER_MIN_GB` | `1` | Minimum free disk space in GB |
| `QBIT_CLEANER_INTERVAL` | `15` | Time between checks, in seconds |
| `QBIT_CLEANER_DRY_RUN` | `false` | Log torrents that would be deleted without deleting them |
| `QBIT_CLEANER_LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

## Example
```
services:
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:5.2.4
    environment:
      - DOCKER_MODS=ghcr.io/maresyp/docker-mod-qbit-cleaner:develop
      - QBIT_CLEANER_HOST=http://localhost:8080
      - QBIT_CLEANER_MIN_GB=10
      - QBIT_CLEANER_INTERVAL=60
      - QBIT_CLEANER_DRY_RUN=true
      - QBIT_CLEANER_LOG_LEVEL=INFO

```
### Note: The cleaner deletes both torrent data and torrent entries when dry-run is disabled. Use dry-run first to verify which torrents would be removed.
