FROM python:3.12-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt /app/requirements.txt
COPY bot/requirements.txt /app/bot_requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r bot_requirements.txt

# Copy application source code
COPY . /app

# Environment port default
ENV PORT=8080

# Command to run FastAPI server on host 0.0.0.0 and port $PORT
CMD exec python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}
