# --- Stage 1: build the React frontend ---
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend + engine ---
FROM python:3.12-slim
WORKDIR /app

# adb lets the server read installed build versions; chromium is for web capture.
RUN apt-get update \
    && apt-get install -y --no-install-recommends android-tools-adb \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt \
    && python -m playwright install --with-deps chromium

COPY . .
COPY --from=web /web/dist ./frontend/dist

# Run from backend/ so the `verifyr` and `server` packages are importable.
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
