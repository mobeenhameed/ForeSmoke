"""FastAPI app: REST endpoints plus the static single-page frontend."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


class RunRequest(BaseModel):
    video_id: str
    zone_config_id: str


def _id(value: str) -> str:
    try:
        return check_id(value)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def create_app(settings: Settings | None = None, *, seed_sample: bool = True) -> FastAPI:
    settings = settings or Settings()
    settings.ensure()
    video_store = video_mod.VideoStore(settings)
    github_videos = GitHubVideoSync(settings)
    zone_store = zone_mod.ZoneStore(settings)
    runs = RunManager(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if seed_sample:
            try:
                seed(settings)
            except Exception:  # never block the UI on the optional sample
                LOG.exception("could not seed the sample run")
        yield
        runs.shutdown()

    app = FastAPI(title="ForeSmoke review", lifespan=lifespan)
    app.state.settings, app.state.runs = settings, runs

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
        return FileResponse(get_video(video_id)["path"])

    @app.get("/api/videos/{video_id}/frame")
    def video_frame(video_id: str, t_ms: int = Query(..., ge=0), width: int = Query(360, ge=64, le=1280)):
        meta = get_video(video_id)
        cache = settings.thumbs_dir / meta["video_id"] / f"{t_ms}-{width}.jpg"
        if not cache.exists():
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
            params=request.params.model_dump())
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
                                   params=request.params.model_dump(), frame_t_ms=request.frame_t_ms)
        except zone_mod.ZoneError as error:
            raise HTTPException(422, str(error)) from error
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
