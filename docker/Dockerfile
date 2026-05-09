FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl openssl cron ca-certificates fonts-noto-cjk && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY pipeline_config.json /app/pipeline_config.json

RUN chmod +x /app/scripts/*.sh

VOLUME /data
EXPOSE 8080

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
