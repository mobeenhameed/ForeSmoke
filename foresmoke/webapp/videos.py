"""Registry of reviewable videos: bundled sample clips plus browser uploads."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import BinaryIO

from .probe import ProbeError, probe_video
from .store import VIDEO_EXTENSIONS, Settings, check_id, new_id, now_iso, read_json, slug, write_json


class VideoStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    # -- sample clips shipped at the repository root (chair.mp4 etc.) -------------------
    def _sample_paths(self) -> dict[str, Path]:
        found = {}
        for path in sorted(self.settings.sample_videos_dir.glob("*")):
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                found[f"sample-{slug(path.stem, 'clip')}"] = path
        return found

    def _manifest_samples(self) -> dict[str, dict]:
        """Fallback metadata for serverless deployments where videos are CDN assets."""
        manifest = self.settings.repo_root / "configs" / "vercel_videos.json"
        try:
            items = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {
            item["video_id"]: item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("video_id"), str)
            and isinstance(item.get("static_url"), str)
        }

    def _meta_path(self, video_id: str) -> Path:
        return self.settings.videos_dir / check_id(video_id) / "meta.json"

    def _sample_meta(self, video_id: str, path: Path) -> dict:
        cached = read_json(self._meta_path(video_id))
        stat = path.stat()
        if cached and cached.get("mtime") == stat.st_mtime and cached.get("size") == stat.st_size:
            return cached
        meta = {"video_id": video_id, "name": path.name, "kind": "sample", "path": str(path),
                "size": stat.st_size, "mtime": stat.st_mtime, "created_at": now_iso(), **probe_video(path)}
        write_json(self._meta_path(video_id), meta)
        return meta

    # -- public API ----------------------------------------------------------------------
    def list(self) -> list[dict]:
        github = []
        uploads = []
        if self.settings.videos_dir.exists():
            for meta_path in self.settings.videos_dir.glob("*/meta.json"):
                meta = read_json(meta_path)
                if not meta or not Path(meta.get("path", "")).exists():
                    continue
                if meta.get("kind") == "github":
                    github.append(meta)
                elif meta.get("kind") == "upload":
                    uploads.append(meta)
        videos = []
        sample_paths = self._sample_paths()
        for video_id, path in sample_paths.items():
            try:
                videos.append(self._sample_meta(video_id, path))
            except (ProbeError, OSError):
                continue
        if not sample_paths:
            videos.extend(self._manifest_samples().values())
        uploads.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        github.sort(key=lambda m: m.get("repo_path", ""))
        return uploads + github + videos

    def get(self, video_id: str) -> dict:
        check_id(video_id)
        samples = self._sample_paths()
        if video_id in samples:
            return self._sample_meta(video_id, samples[video_id])
        if video_id in (manifest := self._manifest_samples()):
            return manifest[video_id]
        meta = read_json(self._meta_path(video_id))
        if not meta or not Path(meta.get("path", "")).exists():
            raise KeyError(video_id)
        return meta

    def save_upload(self, filename: str, stream: BinaryIO) -> dict:
        """Store an upload under a generated id; the client filename is never used as a path."""
        suffix = Path(filename or "").suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise ProbeError(f"Unsupported file type {suffix or '(none)'}; use one of "
                             + ", ".join(sorted(VIDEO_EXTENSIONS)))
        video_id = new_id("upload")
        folder = self.settings.videos_dir / video_id
        folder.mkdir(parents=True, exist_ok=False)
        target = folder / f"source{suffix}"
        try:
            with target.open("wb") as handle:
                shutil.copyfileobj(stream, handle, length=1 << 20)
            info = probe_video(target)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        meta = {"video_id": video_id, "name": Path(filename).name[:120], "kind": "upload",
                "path": str(target), "size": target.stat().st_size, "created_at": now_iso(), **info}
        write_json(folder / "meta.json", meta)
        return meta


def public(meta: dict) -> dict:
    """Strip server paths before sending video metadata to the browser."""
    keys = ("video_id", "name", "kind", "repo_path", "duration_s", "width", "height", "fps", "frame_count", "created_at")
    return {k: meta.get(k) for k in keys}
