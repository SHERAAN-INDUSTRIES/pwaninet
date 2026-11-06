FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies (including ffmpeg and ffprobe for reel/video transcoding)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        build-essential \
        libpq-dev \
        ffmpeg \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/

RUN pip install --no-cache-dir --upgrade pip     && pip install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps chromium

# Copy project
COPY . /app/

# Create logs directory
RUN mkdir -p /app/logs

# Expose port
EXPOSE 8000

# Run the ASGI application with Uvicorn for HTTP and WebSocket support
CMD ["uvicorn", "pwaninet.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
