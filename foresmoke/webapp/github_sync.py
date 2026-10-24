"""Import video blobs from the configured Git remote into the local video catalog.

Uses the checkout's existing Git credentials. The working tree is never replaced: new or
changed remote videos are copied into webapp_data and verified against their Git blob SHA.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading

from .probe import ProbeError, probe_video
from .store import VIDEO_EXTENSIONS, Settings, now_iso, read_json, write_json

MAX_VIDEO_BYTES = 1_000_000_000
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")


class GitHubSyncError(RuntimeError):
    pass


def _git_blob_sha(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class GitHubVideoSync:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()

    def _git(self, *args: str, timeout: int = 90) -> bytes:
        try:
            result = subprocess.run(["git", *args], cwd=self.settings.repo_root,
                                    capture_output=True, check=True, timeout=timeout)
            return result.stdout
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            detail = getattr(error, "stderr", b"") or b""
            message = detail.decode("utf-8", "replace").strip()[-300:] or str(error)
            raise GitHubSyncError(f"GitHub sync failed: {message}") from error

    def _tree(self) -> list[dict]:
        branch = self.settings.github_branch
        if not BRANCH_PATTERN.fullmatch(branch) or ".." in branch:
            raise GitHubSyncError("Invalid GitHub branch setting")
        self._git("fetch", "--quiet", "origin", branch)
        revision = self._git("rev-parse", "FETCH_HEAD").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise GitHubSyncError("Git did not return a valid revision")
        output = self._git("ls-tree", "-r", "-l", "-z", revision)
        entries = []
        for record in output.split(b"\0"):
            if not record:
                continue
            metadata, separator, raw_path = record.partition(b"\t")
            parts = metadata.split()
            if not separator or len(parts) != 4 or parts[1] != b"blob":
                continue
            path = raw_path.decode("utf-8", "replace")
            if PurePosixPath(path).suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                size = int(parts[3])
            except ValueError:
                continue
            entries.append({"path": path, "sha": parts[2].decode(), "size": size})
        return entries

    def _write_blob(self, sha: str, destination: Path, expected_size: int) -> None:
        try:
            with destination.open("wb") as output:
                process = subprocess.Popen(["git", "cat-file", "blob", sha],
                                           cwd=self.settings.repo_root, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE)
                try:
                    total = 0
                    while chunk := process.stdout.read(1 << 20):
                        total += len(chunk)
                        if total > MAX_VIDEO_BYTES:
                            process.kill()
                            raise GitHubSyncError("video exceeded the size limit")
                        output.write(chunk)
                    stderr = process.stderr.read()
                    if process.wait(timeout=30) != 0:
                        raise GitHubSyncError(stderr.decode("utf-8", "replace").strip() or "Git could not read the video")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            if total != expected_size or _git_blob_sha(destination) != sha:
                raise GitHubSyncError("copied video did not match its Git blob hash")
        except OSError as error:
            raise GitHubSyncError(f"Could not cache video: {error}") from error

    def sync(self) -> dict:
        with self._lock:
            entries = self._tree()
            downloaded = 0
            already_available = 0
            errors = []
            for entry in entries:
                path = PurePosixPath(entry["path"])
                if not path.parts or any(part in ("", ".", "..") for part in path.parts):
                    errors.append(f"Invalid repository path: {entry['path']}")
                    continue
                size = entry["size"]
                if size < 1 or size > MAX_VIDEO_BYTES:
                    errors.append(f"Skipped {path.name}: unsupported file size")
                    continue
                if len(path.parts) == 1:
                    local = self.settings.sample_videos_dir / path.name
                    if local.is_file() and local.stat().st_size == size and _git_blob_sha(local) == entry["sha"]:
                        already_available += 1
                        continue
                video_id = "github-" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
                folder = self.settings.videos_dir / video_id
                target = folder / f"source{path.suffix.lower()}"
                meta_path = folder / "meta.json"
                cached = read_json(meta_path)
                if cached and cached.get("git_sha") == entry["sha"] and target.is_file():
                    already_available += 1
                    continue
                folder.mkdir(parents=True, exist_ok=True)
                partial = folder / f".download{path.suffix.lower()}"
                try:
                    self._write_blob(entry["sha"], partial, size)
                    info = probe_video(partial)
                    os.replace(partial, target)
                    write_json(meta_path, {
                        "video_id": video_id, "name": path.name, "kind": "github",
                        "repo_path": str(path), "git_sha": entry["sha"], "path": str(target),
                        "size": size, "created_at": now_iso(), **info,
                    })
                    downloaded += 1
                except (OSError, ProbeError, GitHubSyncError) as error:
                    partial.unlink(missing_ok=True)
                    errors.append(f"{path}: {error}")
            return {"found": len(entries), "downloaded": downloaded,
                    "already_available": already_available, "errors": errors}
