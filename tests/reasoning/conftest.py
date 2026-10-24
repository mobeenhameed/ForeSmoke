from pathlib import Path
import pytest
from foresmoke.reasoning import load_config

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def config():
    return load_config(ROOT / "configs/zones.json")
