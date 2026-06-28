# Dockerfile for Hugging Face Spaces
#
# HF Spaces runs containers as a non-root user (UID 1000) and serves
# on port 7860. Both are requirements — any other port won't be exposed,
# and writing to root-owned directories will fail.
#
# This builds the FastAPI backend only. The React frontend is served
# as static files from FastAPI so we have a single deployment unit.

FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces runs as user 1000 — create it and switch early so all
# subsequent file operations are owned by the right user
RUN useradd -m -u 1000 appuser
USER appuser
WORKDIR /home/appuser

# Copy and install Python dependencies
COPY --chown=appuser backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --user -r requirements.txt

# Build the React frontend
COPY --chown=appuser frontend/ ./frontend/
WORKDIR /home/appuser/frontend
RUN npm install && npm run build

# Copy backend app
WORKDIR /home/appuser
COPY --chown=appuser backend/ ./backend/

# Copy built frontend into a location FastAPI can serve as static files
RUN cp -r frontend/dist backend/static

# HF Spaces uses port 7860
EXPOSE 7860

ENV PATH="/home/appuser/.local/bin:${PATH}"
# Tell vector_store.py to use in-memory ChromaDB
ENV HF_SPACE=true

CMD ["uvicorn", "backend.app.api.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]