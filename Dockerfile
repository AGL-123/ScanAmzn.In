FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scanamzn ./scanamzn

ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
# Mount your config at /app/config.toml and pass secrets with --env-file.
CMD ["python", "-m", "scanamzn", "-c", "/app/config.toml", "run"]
