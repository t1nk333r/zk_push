# Pull agent for ZKTeco devices without ADMS (see README, "pull mode").
# Built from the repository root by deploy/pull-agent/Makefile:
#
#   docker build -f deploy/pull-agent/Dockerfile -t zk-pull-agent .
#
# Runs tools/pull_from_device.py; the state file lives in the /data volume.
FROM python:3.12-slim-bookworm

ARG VERSION=dev
LABEL org.opencontainers.image.title="zk-pull-agent" \
	org.opencontainers.image.description="Pulls ZKTeco attendance logs over the LAN and posts them to ERPNext zk_push" \
	org.opencontainers.image.source="https://github.com/t1nk333r/zk_push" \
	org.opencontainers.image.version="${VERSION}" \
	org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY deploy/pull-agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
	&& useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin agent \
	&& mkdir /data && chown agent /data

COPY tools/pull_from_device.py tools/backfill_from_device.py tools/export_device_users.py /app/
RUN python -c "import sys; sys.path.insert(0, '/app'); import pull_from_device"

USER agent
WORKDIR /data
VOLUME /data
ENTRYPOINT ["python", "/app/pull_from_device.py", "--state", "/data/state.json"]
CMD ["--help"]
