ARG BUILD_FROM=mcr.microsoft.com/playwright/python:v1.47.0-jammy
FROM ${BUILD_FROM}

RUN pip install --no-cache-dir playwright==1.47.0 aiohttp==3.10.5 \
    && python -m playwright install --with-deps chromium

COPY record.py /record.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
