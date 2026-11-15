FROM node:22-bookworm-slim AS web-build
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:stable-alpine AS web
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web-build /build/dist /usr/share/nginx/html

FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/home/trojanclaw \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 PUPPETEER_SKIP_DOWNLOAD=true
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m venv /opt/backend \
    && /opt/backend/bin/pip install --no-cache-dir -r backend/requirements.txt \
    && pip install --no-cache-dir uv==0.11.16
COPY integrations/ /app/integrations/
RUN uv sync --locked --no-dev --project integrations/handshake --python /usr/local/bin/python --no-managed-python \
    && uv sync --locked --no-dev --project integrations/piazza --python /usr/local/bin/python --no-managed-python \
    && npm ci --prefix integrations/brightspace --omit=dev
RUN npm ci --prefix integrations/google-calendar --omit=dev
COPY config/ /app/config/
COPY prompts/ /app/prompts/
COPY backend/app /app/backend/app
RUN useradd --create-home --uid 10001 trojanclaw && chown -R trojanclaw:trojanclaw /app /home/trojanclaw
USER 10001:10001
WORKDIR /app/backend
CMD ["/opt/backend/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "172.30.0.30"]
