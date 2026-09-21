# Dockerfile - UNDRR Chatbot API (FastAPI, uvicorn app.main:app)
FROM python:3.11-slim

WORKDIR /data

# Copy requirements first
COPY requirements.txt /data/

# System deps (minimal, required for compilation and basic utilities)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
 && rm -rf /var/lib/apt/lists/*

# Python dependencies for chatbot
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download and validate spaCy model during build (eager, not lazy loading)
RUN python -m spacy download en_core_web_md && \
    python -c "import spacy; spacy.load('en_core_web_md'); print('✓ spaCy model pre-loaded successfully')"

# Application code (dev compose bind-mounts ./app and ./frontend over these paths)
COPY app/ /data/app/
COPY frontend/ /data/frontend/
# Schema files: initdb uses them via the postgres container; the API re-applies
# the idempotent auth migration (002) at startup for existing databases.
COPY postgres_init/ /data/postgres_init/

# Create necessary directories
RUN mkdir -p /data/chat_session /data/logs

# Expose port for chatbot only
EXPOSE 8000

# Default command: the API (both compose files override this with their own uvicorn flags)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]