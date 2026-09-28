# ---- 前端建置 ----
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- 後端 ----
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 GOLDHUNTER_DATA_DIR=/data
COPY backend/pyproject.toml backend/
COPY backend/goldhunter backend/goldhunter
RUN pip install --no-cache-dir ./backend
COPY --from=web /web/dist web/dist
# 以非 root 使用者執行
RUN useradd -m goldhunter && mkdir -p /data && chown goldhunter /data
USER goldhunter
EXPOSE 8000
WORKDIR /app/backend
CMD ["uvicorn", "goldhunter.main:app", "--host", "0.0.0.0", "--port", "8000"]
