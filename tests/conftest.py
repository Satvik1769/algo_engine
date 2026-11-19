"""Load the untracked .env before test collection so integration skip conditions see credentials."""
from dotenv import load_dotenv

load_dotenv(override=False)
