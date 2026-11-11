ARG UV_VERSION=0.12.15
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

## Buildstage ##
FROM ghcr.io/linuxserver/baseimage-alpine:3.22 AS buildstage

COPY --from=uv /uv /root-layer/usr/local/bin/uv
COPY root/ /root-layer/

RUN find /root-layer/etc/s6-overlay -type f \( -name run -o -name finish -o -name check \) \
        -exec chmod +x {} +

FROM scratch
LABEL maintainer="maresyp.dev@gmail.com"

COPY --from=buildstage /root-layer/ /
