"""Configuration for the optional Kiro visual-review runtime."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class ReviewSettings:
    enabled: bool = False
    cli_executable: str = "kiro-cli"
    model: str = "claude-sonnet-5.5"
    timeout_s: float = 60.0
    candidate_delay_ms: int = 2_000
    cooldown_ms: int = 5_000
    queue_capacity: int = 3
    max_reviews_per_run: int = 10
    evidence_dir: Path | None = None
    clear_reference: Path | None = None
    clear_reference_t_ms: int | None = None
    max_frames: int = 3
    max_image_dimension: int = 1_280
    max_image_bytes: int = 1_500_000
    agent_name: str = "foresmoke-visual-review"

    def validate(self) -> "ReviewSettings":
        if not self.cli_executable.strip():
            raise ValueError("review CLI executable cannot be empty")
        if len(self.cli_executable) > 500:
            raise ValueError("review CLI executable is too long")
        if not self.model.strip() or len(self.model) > 500:
            raise ValueError("review model must contain 1 to 500 characters")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.agent_name):
            raise ValueError("review agent name must be a safe identifier")
        if not 1 <= self.timeout_s <= 900:
            raise ValueError("review timeout must be between 1 and 900 seconds")
        if self.candidate_delay_ms < 0 or self.cooldown_ms < 0:
            raise ValueError("review source-time delays cannot be negative")
        if not 1 <= self.queue_capacity <= 20:
            raise ValueError("review queue capacity must be between 1 and 20")
        if not 1 <= self.max_reviews_per_run <= 100:
            raise ValueError("maximum reviews per run must be between 1 and 100")
        if not 1 <= self.max_frames <= 3:
            raise ValueError("visual review supports one to three recent frames")
        if not 320 <= self.max_image_dimension <= 2_048:
            raise ValueError("review image dimension must be between 320 and 2048")
        if not 50_000 <= self.max_image_bytes <= 5_000_000:
            raise ValueError("review image byte cap must be between 50KB and 5MB")
        if self.clear_reference_t_ms is not None and self.clear_reference_t_ms < 0:
            raise ValueError("clear reference source timestamp cannot be negative")
        if self.clear_reference is not None and not self.clear_reference.is_file():
            raise ValueError(f"clear reference does not exist: {self.clear_reference}")
        return self
