from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
PROFILE_ENV_PATH = os.getenv("BOT_ENV_PROFILE", "").strip()

# Alap .env, majd opcionális profil felülírások
load_dotenv(ENV_PATH, override=False)
if PROFILE_ENV_PATH:
    profile_path = (BASE_DIR / PROFILE_ENV_PATH).resolve()
    if profile_path.exists():
        load_dotenv(profile_path, override=True)


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def env_has_value(name: str) -> bool:
    value = os.getenv(name)
    return value is not None and str(value).strip() != ""


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, str(default)).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw_value = os.getenv(name, default)
    return [part.strip() for part in raw_value.split(",") if part.strip()]
