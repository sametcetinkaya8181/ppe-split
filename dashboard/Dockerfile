# Taban imaj digest ile sabit (python:3.11-slim, 2026-10-28 itibarıyla yerelde kullanılan). Güncellemek için:
#   docker pull python:3.11-slim && docker image inspect python:3.11-slim --format '{{index .RepoDigests 0}}'
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && groupadd --system --gid 10001 ppe && useradd --system --uid 10001 --gid ppe --no-create-home --shell /usr/sbin/nologin ppe
COPY alembic.ini .
COPY alembic ./alembic
COPY db ./db
COPY auth ./auth
COPY ingest ./ingest
COPY maintenance ./maintenance
COPY app.py observability.py ./
COPY static ./static
# Uygulama kodu root'a ait ve salt okunur; süreç ayrıcalıksız kullanıcıyla çalışır (yazma yalnız /data ve /tmp).
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
