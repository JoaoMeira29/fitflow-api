# The FitFlow API as a container image.
FROM python:3.14-slim

# Do not write .pyc files, and print logs straight away instead of buffering.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies are installed before the code is copied. Docker caches each
# step, so changing the code does not reinstall every package.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# Run as an ordinary user, not root.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# 0.0.0.0 makes the server reachable from outside the container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
