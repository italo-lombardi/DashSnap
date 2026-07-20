ARG BUILD_FROM=mcr.microsoft.com/playwright/python:v1.61.0-jammy
FROM ${BUILD_FROM}

# tzdata: base image ships no zoneinfo, so TZ=Europe/Dublin (etc.) can't resolve
# and datetime.now() stays UTC. Required for local-time recording filenames.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir playwright==1.61.0 aiohttp==3.14.1 \
    && python -m playwright install --with-deps chromium

COPY record.py /record.py
COPY icon.png /icon.png
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
