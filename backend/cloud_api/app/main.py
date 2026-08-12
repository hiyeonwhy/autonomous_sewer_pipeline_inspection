from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.db.mongodb import mongodb_lifespan
from app.domain.agent_telemetry.command_router import router as agent_command_router
from app.domain.agent_telemetry.frame_stream import router as agent_frame_stream_router
from app.domain.agent_telemetry.router import router as agent_telemetry_router
from app.domain.defects.router import router as defects_router
from app.domain.drive_logs.router import router as drive_logs_router
from app.domain.media.router import router as media_router
from app.domain.priority.router import router as priority_router
from app.domain.reconstructions.router import router as reconstructions_router
from app.domain.reports.router import router as reports_router
from app.domain.resources.router import router as resources_router
from app.domain.sessions.router import router as sessions_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=mongodb_lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://i15c102.p.ssafy.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_telemetry_router)
app.include_router(agent_frame_stream_router)
app.include_router(agent_command_router)
app.include_router(sessions_router)
app.include_router(defects_router)
app.include_router(drive_logs_router)
app.include_router(reports_router)
app.include_router(resources_router)
app.include_router(reconstructions_router)
app.include_router(media_router)
app.include_router(priority_router)

app.mount(
    "/media",
    StaticFiles(directory=settings.media_root, check_dir=False),
    name="media",
)

@app.get("/api/health")
async def health_check(request: Request) -> dict[str, str]:
    database = request.app.state.database
    await database.command("ping")

    return {
        "status": "ok",
        "server": "connected",
        "mongodb": "connected",
        # 배포 스모크 테스트가 이 값을 방금 빌드한 커밋과 대조한다.
        "commit": settings.git_commit,
    }
