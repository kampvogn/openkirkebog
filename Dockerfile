FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/data

WORKDIR /opt/openkirkebog
COPY . .
RUN pip install --no-cache-dir . \
    && useradd --uid 10001 --create-home openkirkebog \
    && install -d -o openkirkebog -g openkirkebog /data

USER openkirkebog
EXPOSE 8000
CMD ["python", "-m", "app", "serve", "--host", "0.0.0.0"]
