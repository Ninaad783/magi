#!/bin/bash
set -e

echo "Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "Starting Vera Bot on http://localhost:8080"
echo ""
echo "Make sure ANTHROPIC_API_KEY is set. You can put it in a .env file."
echo ""

# Load .env if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

uvicorn main:app --host 0.0.0.0 --port 8080
