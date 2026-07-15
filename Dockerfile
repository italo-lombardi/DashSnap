ARG BUILD_FROM=mcr.microsoft.com/playwright/python:v1.61.0-jammy
FROM ${BUILD_FROM}

RUN pip install --no-cache-dir playwright==1.61.0 aiohttp==3.14.1 \
    && python -m playwright install --with-deps chromium \
    && mkdir -p /media/DashSnap

COPY record.py /record.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
