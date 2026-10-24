"""Spawn and supervise the existing analysis CLI (``python -m foresmoke.demo``).

The runner never interprets observations, states or events. Progress is the growing
observations.jsonl line count against duration_s x target_hz; done/failed comes from the
subprocess exit code.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

from .store import RUN_FILES, Settings, check_id, count_lines, new_id, now_iso, read_json, write_json

ACTIVE = "running"
TERMINAL = ("done", "failed", "cancelled")


def build_command(*, python: str, source: Path, source_fps: float, zones: Path, run_dir: Path,
                  target_hz: float) -> list[str]:
    """The exact CLI contract from the integration brief, as an argv list (no shell)."""
    return [python, "-m", "foresmoke.demo",
            "--source", str(source), "--source-kind", "video",
            "--source-fps", f"{source_fps:g}",
            "--zones", str(zones),
            "--run-dir", str(run_dir),
            "--target-hz", f"{target_hz:g}"]


class RunManager:
    def __init__(self, settings: Settings, python: str | None = None):
        self.settings = settings
        self.python = python or sys.executable
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # -- paths ---------------------------------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        return self.settings.runs_dir / check_id(run_id)

    def meta_dir(self, run_id: str) -> Path:
        return self.settings.run_meta_dir / check_id(run_id)

    def meta(self, run_id: str) -> dict | None:
        return read_json(self.meta_dir(run_id) / "meta.json")

    def _save_meta(self, run_id: str, meta: dict) -> None:
        write_json(self.meta_dir(run_id) / "meta.json", meta)

    # -- lifecycle -----------------------------------------------------------------------
    def start(self, *, video: dict, zone: dict) -> dict:
        run_id = new_id("run")
        run_dir = self.run_dir(run_id)
        if run_dir.exists() and any(run_dir.iterdir()):
            raise RuntimeError("run directory unexpectedly exists; try again")
        meta_dir = self.meta_dir(run_id)
        meta_dir.mkdir(parents=True, exist_ok=False)
        # Snapshot the zone file so later edits can never change what this run used.
        zones_copy = meta_dir / "zones.json"
        shutil.copyfile(zone["path"], zones_copy)
        target_hz = self.settings.target_hz
        command = build_command(python=self.python, source=Path(video["path"]), source_fps=video["fps"],
                                zones=zones_copy, run_dir=run_dir, target_hz=target_hz)
        meta = {
            "run_id": run_id, "state": ACTIVE, "created_at": now_iso(), "started_epoch": None,
            "finished_at": None, "elapsed_s": None, "returncode": None, "error": None,
            "video_id": video["video_id"], "video_name": video.get("name"),
            "duration_s": video["duration_s"], "fps": video["fps"],
            "width": video["width"], "height": video["height"],
            "zone_config_id": zone["config_id"], "zone_name": zone.get("name"),
            "target_hz": target_hz,
            "expected_observations": max(1, math.ceil(video["duration_s"] * target_hz)),
            "command": command, "source": "webapp",
        }
        log = (meta_dir / "process.log").open("wb")
        try:
            proc = subprocess.Popen(command, cwd=self.settings.repo_root, stdout=log,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        except OSError as error:
            log.close()
            meta.update(state="failed", error=f"Could not start analysis: {error}", finished_at=now_iso())
            self._save_meta(run_id, meta)
            return meta
        log.close()
        started = time.monotonic()
        meta["started_epoch"] = time.time()
        with self._lock:
            # Register before publishing metadata so status() never sees an unsupervised run.
            self._procs[run_id] = proc
            self._save_meta(run_id, meta)
        threading.Thread(target=self._watch, args=(run_id, proc, started), daemon=True,
                         name=f"watch-{run_id}").start()
        return meta

    def _watch(self, run_id: str, proc: subprocess.Popen, started: float) -> None:
        code = proc.wait()
        with self._lock:
            self._procs.pop(run_id, None)
            meta = self.meta(run_id) or {"run_id": run_id}
            if meta.get("state") != "cancelled":
                meta["state"] = "done" if code == 0 else "failed"
                if code != 0:
                    meta["error"] = self._failure_reason(run_id, code)
            meta.update(returncode=code, finished_at=now_iso(), elapsed_s=round(time.monotonic() - started, 1))
            self._save_meta(run_id, meta)

    def _failure_reason(self, run_id: str, code: int) -> str:
        tail = self.log_tail(run_id, 12)
        lines = [l for l in tail.splitlines() if "ERROR" in l or "error" in l.lower()]
        detail = lines[-1].strip() if lines else (tail.splitlines()[-1].strip() if tail.strip() else "")
        return f"Analysis exited with status {code}" + (f": {detail}" if detail else "")

    def cancel(self, run_id: str) -> dict:
        with self._lock:
            proc = self._procs.get(run_id)
            meta = self.meta(run_id)
            if meta is None:
                raise KeyError(run_id)
            if proc is None or meta.get("state") != ACTIVE:
                return meta
            meta["state"] = "cancelled"
            meta["error"] = "Cancelled by reviewer"
            self._save_meta(run_id, meta)
        # SIGINT lets demo.py close its files cleanly; escalate if it does not exit.
        for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 3)):
            if proc.poll() is not None:
                break
            proc.send_signal(sig)
            try:
                proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                continue
        if proc.poll() is None:
            proc.kill()
        return self.status(run_id)

    def status(self, run_id: str) -> dict:
        meta = self.meta(run_id)
        if meta is None:
            raise KeyError(run_id)
        with self._lock:
            alive = run_id in self._procs
        if meta.get("state") == ACTIVE and not alive:
            # Server restarted while this run was in flight; nothing is supervising it now.
            meta.update(state="failed", error="Analysis was interrupted (server restarted).")
            self._save_meta(run_id, meta)
        processed = count_lines(self.run_dir(run_id) / "observations.jsonl")
        expected = meta.get("expected_observations") or 1
        if meta["state"] == "done":
            progress = 1.0
        else:
            progress = min(processed / expected, 0.99)
        result = {**meta, "processed_observations": processed, "progress": round(progress, 4),
                  "files": self.available_files(run_id)}
        if meta["state"] == ACTIVE and meta.get("started_epoch"):
            # Wall-clock processing time, reported separately from source-video time.
            result["elapsed_s"] = round(time.time() - meta["started_epoch"], 1)
        return result

    def log_tail(self, run_id: str, lines: int = 40) -> str:
        path = self.meta_dir(run_id) / "process.log"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        # Ultralytics draws progress bars with carriage returns; keep the last redraw only.
        cleaned = [line.rsplit("\r", 1)[-1] for line in text.splitlines()]
        return "\n".join(cleaned[-lines:])

    def available_files(self, run_id: str) -> list[str]:
        folder = self.run_dir(run_id)
        return [name for name in RUN_FILES if (folder / name).exists()]

    # -- listing -------------------------------------------------------------------------
    def list(self) -> list[dict]:
        """Every runs/<id> directory that holds engine output, joined with webapp metadata."""
        ids: set[str] = set()
        for base in (self.settings.runs_dir, self.settings.run_meta_dir):
            if base.exists():
                ids.update(p.name for p in base.iterdir() if p.is_dir())
        runs = []
        for run_id in ids:
            try:
                check_id(run_id)
            except ValueError:
                continue
            folder = self.run_dir(run_id)
            meta = self.meta(run_id)
            if meta is None:
                if not (folder / "states.jsonl").exists():
                    continue
                meta = {"run_id": run_id, "state": "done", "source": "external",
                        "created_at": None, "video_id": None, "zone_config_id": None}
            elif meta.get("state") == ACTIVE:
                meta = self.status(run_id)
            stamp_from = folder if folder.exists() else self.meta_dir(run_id)
            runs.append({**meta, **self._event_counts(folder / "events.jsonl"),
                         "files": self.available_files(run_id),
                         "mtime": stamp_from.stat().st_mtime})
        runs.sort(key=lambda r: r["mtime"], reverse=True)
        return runs

    @staticmethod
    def _event_counts(path: Path) -> dict:
        """Tally event_type values exactly as written; used only for the run list summary."""
        confirmed = resolved = 0
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        kind = json.loads(line).get("event_type")
                    except ValueError:
                        continue
                    confirmed += kind == "incident_confirmed"
                    resolved += kind == "incident_resolved"
        except OSError:
            return {"confirmed_count": None, "resolved_count": None}
        return {"confirmed_count": confirmed, "resolved_count": resolved}

    def shutdown(self) -> None:
        with self._lock:
            procs = list(self._procs.items())
        for run_id, _ in procs:
            try:
                self.cancel(run_id)
            except KeyError:
                pass
