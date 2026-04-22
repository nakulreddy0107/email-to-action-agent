FROM python:3.11-slim

WORKDIR /app

# Install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data dir for SQLite
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_ENV=production \
    DRY_RUN=true

EXPOSE 8000

# Default: run the API server. Override for one-off jobs:
#   docker run ... python main.py --demo
CMD ["python", "main.py", "--serve"]
