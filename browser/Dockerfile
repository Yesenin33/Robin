# Chromium sidecar for the Cloudflare-challenged explorer. System Chromium (Debian) instead of Playwright's
# bundled browsers: ~600 MB instead of ~1.5 GB, which matters on a 10 GB disk.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 CHROMIUM_PATH=/usr/bin/chromium PROFILE_DIR=/data/profile
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-liberation \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py fetcher.py ./
RUN mkdir -p /data/profile
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status==200 else 1)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
