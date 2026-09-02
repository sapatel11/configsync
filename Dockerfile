FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY control_plane ./control_plane
COPY storage_service ./storage_service
COPY agent ./agent

CMD ["python", "-m", "uvicorn", "control_plane.main:app", "--host", "0.0.0.0", "--port", "8000"]
