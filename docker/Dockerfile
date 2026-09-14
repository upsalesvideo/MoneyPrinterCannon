# MoneyPrinterCannon — API + WebUI + Remotion renderer in one image.
# Build:  docker compose build      Run: docker compose up
# NOTE: not yet verified by an actual build (Docker was unavailable on the authoring machine).
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=22

# System deps: ffmpeg, Node 22 (nodesource), Chromium headless-shell libs for Remotion, fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git ffmpeg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
        libasound2 libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgtk-3-0 libx11-6 \
        libx11-xcb1 libxcb1 libxext6 libxi6 libxtst6 libxss1 libglib2.0-0 libexpat1 \
        libatspi2.0-0 libxshmfence1 libu2f-udev libvulkan1 \
        fonts-liberation fonts-noto-color-emoji fonts-noto-core fonts-dejavu-core \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Node deps first (better layer caching).
COPY remotion/package.json remotion/package-lock.json* ./remotion/
RUN cd remotion && (test -f package-lock.json && npm ci --no-audit --no-fund || npm install --no-audit --no-fund)

# Python deps + project.
COPY pyproject.toml README.md* ./
COPY moneyprintercannon ./moneyprintercannon
RUN uv pip install --system --no-cache ".[whisper,publish]"

# Rest of the project (webui, remotion sources, skill, examples).
COPY . .

# Download the Remotion headless browser at build time so the first render is fast and offline-safe.
RUN cd remotion && npx remotion browser ensure --log=error \
    && mkdir -p /app/storage/tasks /app/storage/cache \
    && ln -sfn /app/storage/tasks /app/remotion/public/tasks

ENV CANNON_HOST=0.0.0.0 \
    CANNON_PORT=8787 \
    CANNON_STORAGE=/app/storage

VOLUME ["/app/storage"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8787/api/health || exit 1

CMD ["uvicorn", "moneyprintercannon.api:app", "--host", "0.0.0.0", "--port", "8787"]
