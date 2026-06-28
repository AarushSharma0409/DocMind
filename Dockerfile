# Dockerfile for Hugging Face Spaces
#
# HF Spaces runs containers as a non-root user (UID 1000) and serves
# on port 7860. Both are requirements — any other port won't be exposed,
# and writing to root-owned directories will fail.
#
# This builds the FastAPI backend and React frontend together so the
# whole app is a single deployment unit on HF Spaces.

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

# HF Spaces runs as user 1000
RUN useradd -m -u 1000 appuser
USER appuser

# Install Python dependencies
WORKDIR /home/appuser
COPY --chown=appuser backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --user -r requirements.txt

# Build the React frontend
COPY --chown=appuser frontend/ ./frontend/
WORKDIR /home/appuser/frontend
RUN npm install && npm run build

# Copy backend app
WORKDIR /home/appuser/backend
COPY --chown=appuser backend/ .

# Copy built frontend into backend/static so FastAPI can serve it
RUN mkdir -p static && cp -r /home/appuser/frontend/dist/. static/

ENV PATH="/home/appuser/.local/bin:${PATH}"
ENV HF_SPACE=true

EXPOSE 7860

# Run from /home/appuser/backend so 'app.api.main' resolves correctly
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]