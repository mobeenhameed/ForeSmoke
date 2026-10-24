"""FastAPI app: REST endpoints plus the static single-page frontend."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from foresmoke.scene_setup import SetupSettings, ZoneProposalService
from foresmoke.scene_setup.service import ProposalQueueFull

from . import videos as video_mod
from . import zones as zone_mod
from .github_sync import GitHubSyncError, GitHubVideoSync
from .probe import ProbeError, read_frame_jpeg
from .runner import RunManager
from .sample import seed
from .store import RUN_FILES, Settings, check_id, read_json

LOG = logging.getLogger("foresmoke.webapp")
STATIC = Path(__file__).with_name("static")


class ZoneParams(BaseModel):
    candidate_labels: list[str] = Field(default_factory=lambda: list(zone_mod.PARAM_DEFAULTS["candidate_labels"]))
    confirm_ms: int = zone_mod.PARAM_DEFAULTS["confirm_ms"]
    resolve_ms: int = zone_mod.PARAM_DEFAULTS["resolve_ms"]
    min_confidence: float = zone_mod.PARAM_DEFAULTS["min_confidence"]


class ZoneRequest(BaseModel):
    video_id: str
    name: str = "exit_zone"
    camera_name: str = "cam01"
    view_name: str = "exit-view"
    polygon: list[list[float]]
    params: ZoneParams = Field(default_factory=ZoneParams)
    frame_t_ms: int | None = None
    zone_type: Literal["exit_access", "extinguisher_access"] = "exit_access"
    asset_name: str | None = None
    asset_bbox: list[float] | None = None


class ProposalRequest(BaseModel):
    video_id: str
    frame_t_ms: int = Field(ge=0)
    requested_zone_type: Literal["auto", "exit_access", "extinguisher_access"] = "auto"


class AcceptProposalRequest(BaseModel):
    candidate_key: str
    name: str
    camera_name: str = "cam01"
    view_name: str = "exit-view"
    polygon: list[list[float]]
    params: ZoneParams = Field(default_factory=ZoneParams)
    asset_name: str | None = None


class RunRequest(BaseModel):
    video_id: str
    zone_config_id: str


def _id(value: str) -> str:
    try:
        return check_id(value)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def create_app(
    settings: Settings | None = None,
    *,
    seed_sample: bool = True,
    proposal_provider=None,
) -> FastAPI:
    settings = settings or Settings()
    settings.ensure()
    video_store = video_mod.VideoStore(settings)
    github_videos = GitHubVideoSync(settings)
    zone_store = zone_mod.ZoneStore(settings)
    runs = RunManager(settings)
    proposals = ZoneProposalService(
        SetupSettings(
            root=settings.proposals_dir,
            cli_executable=settings.kiro_cli,
            model=settings.proposal_model,
            timeout_s=settings.proposal_timeout_s,
            queue_capacity=settings.proposal_queue_capacity,
            max_image_dimension=settings.proposal_max_image_dimension,
        ),
        provider=proposal_provider,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if seed_sample:
            try:
                seed(settings)
            except Exception:  # never block the UI on the optional sample
                LOG.exception("could not seed the sample run")
        yield
        proposals.shutdown()
        runs.shutdown()

    app = FastAPI(title="ForeSmoke review", lifespan=lifespan)
    app.state.settings, app.state.runs, app.state.proposals = settings, runs, proposals

    def get_video(video_id: str) -> dict:
        try:
            return video_store.get(_id(video_id))
        except KeyError:
            raise HTTPException(404, "Video not found") from None
        except ProbeError as error:
            raise HTTPException(422, str(error)) from error

    def get_zone(config_id: str) -> dict:
        try:
            return zone_store.get(_id(config_id))
        except KeyError:
            raise HTTPException(404, "Zone configuration not found") from None

    # -- videos ----------------------------------------------------------------------------
    @app.get("/api/videos")
    def list_videos():
        return [video_mod.public(v) for v in video_store.list()]

    @app.post("/api/videos/github-sync")
    def sync_github_videos():
        try:
            return github_videos.sync()
        except GitHubSyncError as error:
            raise HTTPException(502, str(error)) from error

    @app.post("/api/videos")
    def upload_video(file: UploadFile = File(...)):
        try:
            meta = video_store.save_upload(file.filename or "", file.file)
        except ProbeError as error:
            raise HTTPException(422, str(error)) from error
        return video_mod.public(meta)

    @app.get("/api/videos/{video_id}")
    def video_info(video_id: str):
        return video_mod.public(get_video(video_id))

    @app.get("/api/videos/{video_id}/file")
    def video_file(video_id: str):
        # FileResponse honours HTTP Range requests, which the <video> element needs to seek.
        video = get_video(video_id)
        if static_url := video.get("static_url"):
            return RedirectResponse(static_url, status_code=307)
        return FileResponse(video["path"])

    @app.get("/api/videos/{video_id}/frame")
    def video_frame(video_id: str, t_ms: int = Query(..., ge=0), width: int = Query(360, ge=64, le=1280)):
        meta = get_video(video_id)
        cache = settings.thumbs_dir / meta["video_id"] / f"{t_ms}-{width}.jpg"
        if cache.exists():
            return FileResponse(cache, media_type="image/jpeg")
        bundled = settings.repo_root / "webapp_data" / "thumbs" / meta["video_id"] / f"{t_ms}-{width}.jpg"
        if bundled.exists():
            return FileResponse(bundled, media_type="image/jpeg")
        if "path" not in meta:
            raise HTTPException(404, "This precomputed deployment does not include that preview frame")
        try:
            data = read_frame_jpeg(meta["path"], t_ms, width)
        except ProbeError as error:
            raise HTTPException(422, str(error)) from error
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return FileResponse(cache, media_type="image/jpeg")

    # -- zones -----------------------------------------------------------------------------
    @app.get("/api/zones")
    def list_zones():
        return [zone_mod.public(z) for z in zone_store.list()]

    @app.get("/api/zones/{config_id}")
    def zone_info(config_id: str):
        return zone_mod.public(get_zone(config_id))

    def _build(request: ZoneRequest) -> tuple[dict, dict]:
        video = get_video(request.video_id)
        config = zone_mod.build_zone_config(
            camera_id="cam01", view_id="preview", width=video["width"], height=video["height"],
            zone_id=zone_mod.slug(request.name, "exit_zone"), polygon=request.polygon,
            params=request.params.model_dump(), zone_type=request.zone_type,
            asset_id=(
                zone_mod.slug(f"{request.name}-extinguisher", "fire-extinguisher")
                if request.zone_type == "extinguisher_access" else None
            ),
            asset_name=request.asset_name, asset_bbox=request.asset_bbox)
        return video, config

    @app.post("/api/zones/validate")
    def validate_zone(request: ZoneRequest):
        _video, config = _build(request)
        try:
            zone_mod.validate_zone_config(config)
        except zone_mod.ZoneError as error:
            raise HTTPException(422, str(error)) from error
        return {"valid": True}

    @app.post("/api/zones")
    def save_zone(request: ZoneRequest):
        video = get_video(request.video_id)
        try:
            item = zone_store.save(video=video, name=request.name, camera_name=request.camera_name,
                                   view_name=request.view_name, polygon=request.polygon,
                                   params=request.params.model_dump(), frame_t_ms=request.frame_t_ms,
                                   zone_type=request.zone_type, asset_name=request.asset_name,
                                   asset_bbox=request.asset_bbox)
        except zone_mod.ZoneError as error:
            raise HTTPException(422, str(error)) from error
        return zone_mod.public(item)

    # -- AI-assisted zone proposals ---------------------------------------------------------
    @app.post("/api/zone-proposals")
    def create_zone_proposal(request: ProposalRequest):
        video = get_video(request.video_id)
        try:
            return proposals.submit(
                video=video,
                requested_t_ms=request.frame_t_ms,
                requested_zone_type=request.requested_zone_type,
            )
        except ProposalQueueFull as error:
            raise HTTPException(429, str(error)) from error

    @app.get("/api/zone-proposals/{job_id}")
    def zone_proposal_status(job_id: str):
        try:
            return proposals.status(_id(job_id))
        except KeyError:
            raise HTTPException(404, "Zone proposal not found") from None

    @app.get("/api/zone-proposals/{job_id}/frame")
    def zone_proposal_frame(job_id: str):
        try:
            record = proposals.status(_id(job_id))
        except KeyError:
            raise HTTPException(404, "Zone proposal not found") from None
        path = settings.proposals_dir / record["job_id"] / "submitted_frame.jpg"
        if record.get("source_frame") is None or not path.exists():
            raise HTTPException(404, "Proposal frame is not available yet")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/zone-proposals/{job_id}/cancel")
    def cancel_zone_proposal(job_id: str):
        try:
            return proposals.cancel(_id(job_id))
        except KeyError:
            raise HTTPException(404, "Zone proposal not found") from None

    @app.post("/api/zone-proposals/{job_id}/accept")
    def accept_zone_proposal(job_id: str, request: AcceptProposalRequest):
        try:
            proposal = proposals.status(_id(job_id))
        except KeyError:
            raise HTTPException(404, "Zone proposal not found") from None
        if proposal["status"] != "completed":
            raise HTTPException(409, "Only a completed proposal can be accepted")
        candidate = next(
            (
                item for item in proposal["candidates"]
                if item["candidate_key"] == request.candidate_key
            ),
            None,
        )
        if candidate is None:
            raise HTTPException(404, "Proposal candidate not found")
        if not candidate["valid"] or candidate["access_polygon"] is None:
            raise HTTPException(422, "This candidate has no valid access polygon")
        video = get_video(proposal["video_id"])
        source = proposal["source_frame"] or {}
        proposed = [[round(float(x), 6), round(float(y), 6)] for x, y in candidate["access_polygon"]]
        accepted = [[round(float(x), 6), round(float(y), 6)] for x, y in request.polygon]
        provenance = {
            "job_id": job_id,
            "candidate_key": request.candidate_key,
            "requested_model": proposal["requested_model"],
            "source_timestamp_ms": source.get("source_timestamp_ms"),
            "source_frame_index": source.get("source_frame_index"),
            "submitted_image_sha256": source.get("submitted_image_sha256"),
            "user_edited": accepted != proposed,
        }
        try:
            item = zone_store.save(
                video=video,
                name=request.name,
                camera_name=request.camera_name,
                view_name=request.view_name,
                polygon=accepted,
                params=request.params.model_dump(),
                frame_t_ms=source.get("source_timestamp_ms"),
                zone_type=candidate["zone_type"],
                asset_name=(
                    request.asset_name or candidate["label"]
                    if candidate["zone_type"] == "extinguisher_access"
                    else None
                ),
                asset_bbox=candidate["asset_bbox"],
                provenance=provenance,
            )
        except zone_mod.ZoneError as error:
            raise HTTPException(422, str(error)) from error
        proposals.record_acceptance(
            job_id,
            candidate_key=request.candidate_key,
            config_id=item["config_id"],
            user_edited=provenance["user_edited"],
        )
        return zone_mod.public(item)

    # -- runs ------------------------------------------------------------------------------
    @app.get("/api/runs")
    def list_runs():
        return [{k: v for k, v in r.items() if k != "command"} for r in runs.list()]

    @app.post("/api/runs")
    def start_run(request: RunRequest):
        video, zone = get_video(request.video_id), get_zone(request.zone_config_id)
        reference = zone["config"]["reference_frame"]
        if (reference["width"], reference["height"]) != (video["width"], video["height"]):
            raise HTTPException(409, (
                f"This zone was drawn on a {reference['width']}x{reference['height']} frame but the "
                f"video is {video['width']}x{video['height']}. Draw a zone on this video first."))
        return runs.start(video=video, zone=zone)

    def get_run(run_id: str) -> dict:
        _id(run_id)
        try:
            return runs.status(run_id)
        except KeyError:
            folder = settings.runs_dir / run_id
            if (folder / "states.jsonl").exists():
                return {"run_id": run_id, "state": "done", "source": "external", "video_id": None,
                        "zone_config_id": None, "files": runs.available_files(run_id), "progress": 1.0}
            raise HTTPException(404, "Run not found") from None

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str):
        return {k: v for k, v in get_run(run_id).items() if k != "command"}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        get_run(run_id)
        try:
            return runs.cancel(run_id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
    def run_log(run_id: str):
        get_run(run_id)
        return runs.log_tail(run_id, 60)

    @app.get("/api/runs/{run_id}/zones")
    def run_zones(run_id: str):
        get_run(run_id)
        config = read_json(runs.meta_dir(run_id) / "zones.json")
        if config is None:
            raise HTTPException(404, "No zone configuration recorded for this run")
        return config

    def _jsonl(run_id: str, name: str):
        get_run(run_id)
        path = runs.run_dir(run_id) / name
        if name not in RUN_FILES or not path.exists():
            raise HTTPException(404, f"{name} not available")
        # Served byte-for-byte: field names and values are exactly what the CLI wrote.
        return FileResponse(path, media_type="application/x-ndjson",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/runs/{run_id}/observations")
    def run_observations(run_id: str):
        return _jsonl(run_id, "observations.jsonl")

    @app.get("/api/runs/{run_id}/states")
    def run_states(run_id: str):
        return _jsonl(run_id, "states.jsonl")

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str):
        return _jsonl(run_id, "events.jsonl")

    @app.get("/api/runs/{run_id}/reviews")
    def run_reviews(run_id: str):
        """Append-only status history; fold by review_id to obtain the latest result."""
        return _jsonl(run_id, "reviews.jsonl")

    @app.get("/api/runs/{run_id}/thumb")
    def run_thumb(run_id: str, t_ms: int = Query(..., ge=0), width: int = Query(360, ge=64, le=1280)):
        meta = get_run(run_id)
        if not meta.get("video_id"):
            raise HTTPException(404, "This run has no source video")
        return video_frame(meta["video_id"], t_ms, width)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(ValueError)
    async def value_error(_request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    return app
