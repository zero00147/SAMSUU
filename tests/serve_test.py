"""Boot the samsu app against a throwaway database, on a spare port.

Keeps the real data/chats.db untouched and never starts the Telegram bot.
"""
import sys
from pathlib import Path

ROOT = Path("/Users/rifat/Desktop/samsu")
sys.path.insert(0, str(ROOT))

from server import db, telegram  # noqa: E402

db.DB_PATH = Path(sys.argv[1])
telegram.load_token = lambda: None  # bot disabled for the test run

import uvicorn  # noqa: E402
from server.main import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
