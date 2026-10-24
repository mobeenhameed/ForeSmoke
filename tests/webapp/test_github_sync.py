"""Git-backed video sync imports newly pushed footage without changing the checkout."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from foresmoke.webapp.github_sync import GitHubVideoSync
from foresmoke.webapp.server import create_app
from foresmoke.webapp.store import Settings
from foresmoke.webapp.videos import VideoStore


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def clip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for i in range(12):
        writer.write(np.full((48, 64, 3), 20 + i, dtype=np.uint8))
    writer.release()
    assert path.stat().st_size > 0


def test_sync_imports_new_push_and_reuses_cached_video(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git is required for repository video sync")
    repo = tmp_path / "checkout"
    remote = tmp_path / "remote.git"
    samples = tmp_path / "samples"
    repo.mkdir()
    samples.mkdir()
    git("init", "-q", cwd=repo)
    git("checkout", "-q", "-b", "webapp-mvp", cwd=repo)
    git("config", "user.name", "ForeSmoke test", cwd=repo)
    git("config", "user.email", "test@example.invalid", cwd=repo)
    clip(repo / "chair.mp4")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "first video", cwd=repo)
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("push", "-q", "origin", "webapp-mvp", cwd=repo)

    settings = Settings(repo_root=repo, sample_videos_dir=samples, data_dir=tmp_path / "data",
                        runs_dir=tmp_path / "runs")
    settings.ensure()
    sync = GitHubVideoSync(settings)
    assert sync.sync() == {"found": 1, "downloaded": 1, "already_available": 0, "errors": []}
    assert sync.sync() == {"found": 1, "downloaded": 0, "already_available": 1, "errors": []}

    clip(repo / "footage" / "second.mp4")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "new footage", cwd=repo)
    git("push", "-q", "origin", "webapp-mvp", cwd=repo)
    with TestClient(create_app(settings, seed_sample=False)) as client:
        response = client.post("/api/videos/github-sync")
        assert response.status_code == 200
        assert response.json() == {"found": 2, "downloaded": 1, "already_available": 1, "errors": []}
        assert len(client.get("/api/videos").json()) == 2
    videos = VideoStore(settings).list()
    assert {video["repo_path"] for video in videos} == {"chair.mp4", "footage/second.mp4"}
    assert all(Path(video["path"]).is_file() for video in videos)
