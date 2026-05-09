#!/bin/bash
set -e

echo "[entrypoint] starting wechat-autopub service..."

# Create data directories
mkdir -p /data/{daily_pipeline,logs,state,health}

# Copy content_log.md to data volume if not exists
if [ ! -f /data/content_log.md ]; then
    if [ -f /app/content_log.md ]; then
        cp /app/content_log.md /data/content_log.md
    else
        echo "# Content Log" > /data/content_log.md
    fi
fi

# Configure cron schedule (default: 08:03 weekdays, Asia/Shanghai)
CRON_SCHEDULE="${PIPELINE_CRON:-3 8 * * 1-5}"

# Write cron job
cat > /etc/cron.d/autopub <<EOF
${CRON_SCHEDULE} root cd /app && python3 src/orchestrator.py >> /data/logs/pipeline.log 2>&1
EOF

chmod 644 /etc/cron.d/autopub
crontab /etc/cron.d/autopub

echo "[entrypoint] cron schedule: ${CRON_SCHEDULE}"
echo "[entrypoint] starting cron daemon..."
cron

echo "[entrypoint] starting callback server on :8080..."
exec python3 /app/src/callback_server.py
