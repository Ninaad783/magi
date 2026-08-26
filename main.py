import os
import sys

# Ensure bot directory is in python module search path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bot"))

from main import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
