import os
import sys

# Ensure bot directory is in python module search path
bot_dir = os.path.join(os.path.dirname(__file__), "bot")
if bot_dir not in sys.path:
    sys.path.insert(0, bot_dir)

import main as bot_module

app = bot_module.app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
