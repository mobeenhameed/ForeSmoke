"""Filesystem layout, identifiers and small JSON helpers for the webapp."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[2]
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
RUN_FILES = ("observations.jsonl", "states.jsonl", "events.jsonl", "reviews.jsonl")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


@dataclass(frozen=True)
class Settings:
    """Where the webapp reads and writes. Tests point these at temporary directories."""

    repo_root: Path = REPO_ROOT
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("FORESMOKE_WEBAPP_DATA", REPO_ROOT / "webapp_data")))
    runs_dir: Path = field(default_factory=lambda: Path(os.environ.get("FORESMOKE_RUNS_DIR", REPO_ROOT / "runs")))
    sample_videos_dir: Path = REPO_ROOT
    bundled_zones: Path = REPO_ROOT / "configs" / "zones.json"
    target_hz: float = 5.0
    visual_review_enabled: bool = field(
        default_factory=lambda: os.environ.get("FORESMOKE_VISUAL_REVIEW", "").lower()
        in ("1", "true", "yes", "on")
    )
    kiro_cli: str = field(
        default_factory=lambda: os.environ.get("FORESMOKE_KIRO_CLI", "kiro-cli")
    )
    review_model: str = field(
        default_factory=lambda: os.environ.get(
            "FORESMOKE_REVIEW_MODEL", "claude-sonnet-5.5"
        )
    )
    review_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("FORESMOKE_REVIEW_TIMEOUT_S", "60"))
    )
    review_candidate_delay_ms: int = field(
        default_factory=lambda: int(
            os.environ.get("FORESMOKE_REVIEW_CANDIDATE_DELAY_MS", "2000")
        )
    )
    review_cooldown_ms: int = field(
        default_factory=lambda: int(
            os.environ.get("FORESMOKE_REVIEW_COOLDOWN_MS", "5000")
        )
    )
    review_queue_capacity: int = field(
        default_factory=lambda: int(
            os.environ.get("FORESMOKE_REVIEW_QUEUE_CAPACITY", "3")
        )
    )
    review_max_per_run: int = field(
        default_factory=lambda: int(
            os.environ.get("FORESMOKE_REVIEW_MAX_PER_RUN", "10")
        )
    )
    review_clear_reference: Path | None = field(
        default_factory=lambda: (
            Path(value)
            if (value := os.environ.get("FORESMOKE_REVIEW_CLEAR_REFERENCE"))
            else None
        )
    )

    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def zones_dir(self) -> Path:
        return self.data_dir / "zones"

    @property
    def run_meta_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    def ensure(self) -> None:
        for path in (self.videos_dir, self.zones_dir, self.run_meta_dir, self.thumbs_dir, self.runs_dir):
            path.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def check_id(value: str) -> str:
    """Reject anything that could escape a directory when used as a path component."""
    if not isinstance(value, str) or not ID_PATTERN.match(value) or ".." in value:
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def slug(value: str, fallback: str) -> str:
    """Turn a human name into a contract Identifier (^[A-Za-z0-9][A-Za-z0-9_.-]*$)."""
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", (value or "").strip()).strip("-_.")
    text = re.sub(r"-{2,}", "-", text)[:48]
    return text if text and ID_PATTERN.match(text) else fallback


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: dict) -> None:
    """Atomic write so a polling reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def count_lines(path: Path) -> int:
    """Count complete JSONL lines; a missing file counts as zero."""
    try:
        with path.open("rb") as handle:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 16), b""))
    except OSError:
        return 0
