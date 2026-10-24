"""Bounded asynchronous scene-setup jobs with independent process ownership."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, Full, Queue
import threading
from typing import Literal

from foresmoke.review.kiro_cli import KiroReviewError
from foresmoke.webapp.store import new_id, now_iso, read_json, write_json

from .config import SetupSettings
from .evidence import SetupEvidenceCapture
from .kiro import KiroSetupAdapter

RequestedZoneType = Literal["auto", "exit_access", "extinguisher_access"]


class ProposalQueueFull(RuntimeError):
    pass


@dataclass
class _Work:
    job_id: str
    video: dict
    requested_t_ms: int
    requested_zone_type: RequestedZoneType


class ZoneProposalService:
    def __init__(self, settings: SetupSettings, provider=None):
        self.settings = settings.validate()
        self.settings.root.mkdir(parents=True, exist_ok=True)
        self.provider = provider or KiroSetupAdapter(self.settings)
        self.capture = SetupEvidenceCapture(self.settings)
        self._queue: Queue[_Work | None] = Queue(maxsize=self.settings.queue_capacity)
        self._lock = threading.RLock()
        self._records: dict[str, dict] = {}
        self._cancelled: set[str] = set()
        self._active_job: str | None = None
        self._closed = False
        self._load_and_reconcile()
        self._worker = threading.Thread(
            target=self._run, name="foresmoke-zone-proposals", daemon=True
        )
        self._worker.start()

    def _job_path(self, job_id: str) -> Path:
        return self.settings.root / job_id / "job.json"

    def _load_and_reconcile(self) -> None:
        for path in self.settings.root.glob("*/job.json"):
            record = read_json(path)
            if not record or not isinstance(record.get("job_id"), str):
                continue
            if record.get("status") in {"pending", "running"}:
                record["status"] = "failed"
                record["error"] = {
                    "code": "interrupted",
                    "message": "Proposal job was interrupted by a server restart.",
                }
                record["updated_at"] = now_iso()
                write_json(path, record)
            self._records[record["job_id"]] = record

    def submit(
        self,
        *,
        video: dict,
        requested_t_ms: int,
        requested_zone_type: RequestedZoneType,
    ) -> dict:
        key = f"{video['video_id']}:{requested_t_ms}:{requested_zone_type}"
        with self._lock:
            if self._closed:
                raise RuntimeError("proposal service is shut down")
            for record in self._records.values():
                if record.get("dedupe_key") == key and record.get("status") in {
                    "pending", "running"
                }:
                    return self._public(record)
            job_id = new_id("proposal")
            record = {
                "schema_version": "1.0",
                "job_id": job_id,
                "status": "pending",
                "video_id": video["video_id"],
                "requested_timestamp_ms": requested_t_ms,
                "requested_zone_type": requested_zone_type,
                "provider": "kiro_cli",
                "requested_model": self.settings.model,
                "reported_model": None,
                "latency_ms": None,
                "source_frame": None,
                "scene_status": None,
                "scene_limitations": [],
                "candidates": [],
                "error": None,
                "acceptances": [],
                "dedupe_key": key,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            work = _Work(job_id, dict(video), requested_t_ms, requested_zone_type)
            try:
                self._queue.put_nowait(work)
            except Full as error:
                raise ProposalQueueFull("The access-area suggestion queue is full.") from error
            self._records[job_id] = record
            self._persist(record)
            return self._public(record)

    def status(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._records:
                raise KeyError(job_id)
            return self._public(self._records[job_id])

    def cancel(self, job_id: str) -> dict:
        should_cancel_process = False
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise KeyError(job_id)
            if record["status"] in {"completed", "failed", "cancelled"}:
                return self._public(record)
            self._cancelled.add(job_id)
            record["status"] = "cancelled"
            record["error"] = {"code": "cancelled", "message": "Proposal cancelled by user."}
            record["updated_at"] = now_iso()
            self._persist(record)
            should_cancel_process = self._active_job == job_id
        if should_cancel_process:
            self.provider.cancel_active()
        return self.status(job_id)

    def record_acceptance(
        self,
        job_id: str,
        *,
        candidate_key: str,
        config_id: str,
        user_edited: bool,
    ) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise KeyError(job_id)
            record["acceptances"].append({
                "candidate_key": candidate_key,
                "config_id": config_id,
                "user_edited": user_edited,
                "accepted_at": now_iso(),
            })
            record["updated_at"] = now_iso()
            self._persist(record)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = self._active_job
            for job_id, record in self._records.items():
                if record["status"] in {"pending", "running"}:
                    self._cancelled.add(job_id)
                    record["status"] = "failed"
                    record["error"] = {
                        "code": "shutdown",
                        "message": "Proposal stopped because the server is shutting down.",
                    }
                    record["updated_at"] = now_iso()
                    self._persist(record)
        if active is not None:
            self.provider.cancel_active()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._worker.join(timeout=8)

    def _run(self) -> None:
        while True:
            try:
                work = self._queue.get(timeout=0.2)
            except Empty:
                with self._lock:
                    if self._closed:
                        return
                continue
            if work is None:
                return
            with self._lock:
                if work.job_id in self._cancelled:
                    self._queue.task_done()
                    continue
                record = self._records[work.job_id]
                record["status"] = "running"
                record["updated_at"] = now_iso()
                self._active_job = work.job_id
                self._persist(record)
            try:
                evidence = self.capture.create(
                    job_id=work.job_id,
                    video=work.video,
                    requested_t_ms=work.requested_t_ms,
                    requested_zone_type=work.requested_zone_type,
                )
                with self._lock:
                    record = self._records[work.job_id]
                    record["source_frame"] = {
                        key: evidence.manifest[key]
                        for key in (
                            "requested_timestamp_ms",
                            "source_timestamp_ms",
                            "source_frame_index",
                            "original_width",
                            "original_height",
                            "decoded_frame_sha256",
                            "submitted_width",
                            "submitted_height",
                            "submitted_image_sha256",
                            "transform",
                            "model_image_limits",
                        )
                    }
                    self._persist(record)
                result = self.provider.propose(evidence)
                with self._lock:
                    record = self._records[work.job_id]
                    if work.job_id in self._cancelled:
                        continue
                    proposal = result.proposal.model_dump(mode="json")
                    record.update({
                        "status": "completed",
                        "scene_status": proposal["scene_status"],
                        "scene_limitations": proposal["scene_limitations"],
                        "candidates": proposal["candidates"],
                        "latency_ms": result.latency_ms,
                        "reported_model": result.reported_model,
                        "error": None,
                        "updated_at": now_iso(),
                    })
                    self._persist(record)
            except KiroReviewError as error:
                self._fail(work.job_id, error.code, error.diagnostic)
            except Exception as error:
                self._fail(work.job_id, "setup_failed", str(error))
            finally:
                with self._lock:
                    if self._active_job == work.job_id:
                        self._active_job = None
                self._queue.task_done()

    def _fail(self, job_id: str, code: str, message: str) -> None:
        with self._lock:
            if job_id in self._cancelled:
                return
            record = self._records[job_id]
            safe = str(message).replace(str(self.settings.root), "[evidence]")
            record["status"] = "failed"
            record["error"] = {"code": code, "message": safe[-1200:]}
            record["updated_at"] = now_iso()
            self._persist(record)

    def _persist(self, record: dict) -> None:
        path = self._job_path(record["job_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, record)

    @staticmethod
    def _public(record: dict) -> dict:
        result = json.loads(json.dumps(record))
        result.pop("dedupe_key", None)
        return result
