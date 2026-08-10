# syntax=docker/dockerfile:1
#
# Constellation — all-in-one (AIO) image. See issue #27.
#
# NOTE: the build CONTEXT is the repo root (see docker-compose.yml), not this
# folder. So the COPY paths below reference engine/, web/, package.json, etc. at
# the repo root. The frontend is built in stage 1; stage 2 is the slim Python
# runtime that serves it via server.py (web/dist/).

# ── Stage 1: build the Vite frontend ──────────────────────────────
FROM node:20-alpine AS web-build
WORKDIR /build
# Deps first (cacheable layer; npm ci requires package-lock.json)
COPY package.json package-lock.json ./
RUN npm ci
# Vite config + sources (vite.config.js root: "web" -> builds to web/dist/)
COPY vite.config.js ./
COPY web/ ./web/
RUN npm run build

# ── Stage 2: Python runtime ───────────────────────────────────────
FROM python:3.12-slim

# git is required at runtime: project_store.py shallow-clones repos from git URLs
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (cacheable layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application
COPY engine/ ./engine/
COPY server.py ./
# Built UI from stage 1 (server.py serves web/dist/)
COPY --from=web-build /build/web/dist/ ./web/dist/
# Demo repos seed the two demo projects on first run
COPY tests/repos/ ./tests/repos/

# Entrypoint lives alongside this Dockerfile in docker/
COPY docker/entrypoint.sh ./entrypoint.sh
# Strip any CRLF (Windows checkout safety) then make executable
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONSTELLATION_PORT=8765

# output/ holds graphs + cloned repos — mount as a volume so it persists
RUN mkdir -p /app/output
VOLUME /app/output

EXPOSE 8765

CMD ["./entrypoint.sh"]
