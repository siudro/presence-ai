# PRESENCE AI on Railway (or any Docker host).
# The app is pure Python standard library: no pip install step.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PRESENCE_SKIP_STARTUP_TESTS=1

WORKDIR /app
COPY presence/ /app/

# Railway injects $PORT; app.py reads it (defaults to 8080 locally).
EXPOSE 8080
CMD ["python", "app.py"]
