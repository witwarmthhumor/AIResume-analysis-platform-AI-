FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

EXPOSE 8000
# --proxy-headers：信任 compose 内网 Nginx 转发的 X-Forwarded-For，
# 让 usage_logs 记录真实客户端 IP（backend 端口不对外发布，仅 Nginx 可达）
CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ip=*"]
