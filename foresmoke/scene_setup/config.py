"""Configuration for asynchronous Kiro access-area proposals."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class SetupSettings:
    root: Path
    cli_executable: str = "kiro-cli"
    model: str = "claude-sonnet-5.5"
    timeout_s: float = 90.0
    queue_capacity: int = 3
    max_image_dimension: int = 1280
    max_image_bytes: int = 1_500_000
    min_area_ratio: float = 0.002
    large_area_ratio: float = 0.70
    threshold_tolerance_ratio: float = 0.04
    agent_name: str = "foresmoke-zone-setup"

    def validate(self) -> "SetupSettings":
        if not self.cli_executable.strip() or len(self.cli_executable) > 500:
            raise ValueError("setup CLI executable must contain 1 to 500 characters")
        if not self.model.strip() or len(self.model) > 500:
            raise ValueError("setup model must contain 1 to 500 characters")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.agent_name):
            raise ValueError("setup agent name must be a safe identifier")
        if not 1 <= self.timeout_s <= 900:
            raise ValueError("setup timeout must be between 1 and 900 seconds")
        if not 1 <= self.queue_capacity <= 20:
            raise ValueError("setup queue capacity must be between 1 and 20")
        if not 320 <= self.max_image_dimension <= 1568:
            raise ValueError("setup image dimension must be between 320 and 1568")
        if not 50_000 <= self.max_image_bytes <= 5_000_000:
            raise ValueError("setup image byte cap must be between 50KB and 5MB")
        if not 0 < self.min_area_ratio < self.large_area_ratio <= 1:
            raise ValueError("setup area thresholds are invalid")
        if not 0 < self.threshold_tolerance_ratio <= 0.25:
            raise ValueError("threshold tolerance must be in (0, 0.25]")
        return self
