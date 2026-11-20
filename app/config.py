import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    directory: Path
    api_key: str = ""
    model: str = ""
    workers: int = 1
    pricing_file: Path | None = None

    @classmethod
    def from_env(cls):
        load_dotenv()
        workers = int(os.getenv("AI_WORKERS", "1"))
        if workers < 1:
            raise ValueError("AI_WORKERS must be at least 1.")
        pricing = os.getenv("AI_PRICING_FILE")
        return cls(
            Path(os.getenv("APP_DATA_DIR", "./data")).resolve(),
            os.getenv("OPENAI_API_KEY", "").strip(),
            os.getenv("OPENAI_MODEL", "").strip(),
            workers,
            Path(pricing).resolve() if pricing else None,
        )
