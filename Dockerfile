FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser
USER appuser
WORKDIR /home/appuser

# Python dependencies
COPY --chown=appuser backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --user -r requirements.txt

# Build React frontend
COPY --chown=appuser frontend/ ./frontend/
WORKDIR /home/appuser/frontend
RUN npm install && npm run build

# Copy backend
WORKDIR /home/appuser/backend
COPY --chown=appuser backend/ .

# Copy React build into /home/appuser/static
# main.py looks for static/ at Path(__file__).parent.parent.parent / "static"
# __file__ = /home/appuser/backend/app/api/main.py
# .parent.parent.parent = /home/appuser/backend
# so static must be at /home/appuser/backend/static
RUN cp -r /home/appuser/frontend/dist /home/appuser/backend/static

ENV PATH="/home/appuser/.local/bin:${PATH}"
ENV HF_SPACE=true

EXPOSE 7860

WORKDIR /home/appuser/backend
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]