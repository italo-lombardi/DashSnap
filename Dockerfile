# Debian bookworm's chromium is built with proprietary_codecs=true +
# ffmpeg_branding="Chrome", so it ships H264 for BOTH amd64 and arm64.
# Playwright's bundled Chromium has no H264, and Google Chrome has no arm64
# Linux build — so live camera streams (e.g. Nest WebRTC, which requires
# H264) only render in the Debian chromium. We drive it via executable_path.
# tzdata: without a zoneinfo db, TZ can't resolve and filenames stay UTC.
ARG BUILD_FROM=docker.io/library/debian:bookworm-slim
FROM ${BUILD_FROM}

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        chromium \
        ffmpeg \
        fonts-liberation \
        fonts-noto-color-emoji \
        tzdata \
        ca-certificates \
        python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

# PEP 668: bookworm's system python is externally-managed; use a venv.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Playwright Python lib only — no chromium download (we use system chromium),
# but Playwright's video recording needs its own ffmpeg helper binary.
# Pin install path so it's HOME-independent (supervisor sets HOME=/, not /root).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN pip install --no-cache-dir playwright==1.61.0 aiohttp==3.14.1 \
    && python3 -m playwright install ffmpeg

COPY record.py /record.py
COPY icon.png /icon.png
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Supervisor passes the addon version as a build-arg; promote it to a runtime
# env so the startup log can print which version is actually running.
ARG BUILD_VERSION
ENV BUILD_VERSION=${BUILD_VERSION}

CMD ["/run.sh"]
