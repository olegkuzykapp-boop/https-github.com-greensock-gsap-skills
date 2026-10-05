import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    description: str
    price_stars: int
    chat_id: int  # закритий канал/група з матеріалами курсу


def load_courses(path: Path = ROOT / "courses.json") -> dict[str, Course]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {c["id"]: Course(**c) for c in data}


BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
CHANNEL_URL = os.getenv("CHANNEL_URL", "")
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "bot.db"))
COURSES = load_courses()
