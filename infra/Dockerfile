FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && groupadd --gid 10001 zhiya \
    && useradd --uid 10001 --gid zhiya --no-create-home zhiya \
    && mkdir /var/lib/zhiya && chown zhiya:zhiya /var/lib/zhiya
COPY backend/app /app/backend/app
COPY backend/smoke_model.py backend/smoke_voice.py /app/backend/
COPY database/*.py database/schema.sql /app/database/
COPY data/seed/*.json /app/data/seed/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
