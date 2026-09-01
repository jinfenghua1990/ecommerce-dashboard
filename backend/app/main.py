from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

# 局域网信任模式：仅内网暴露，CORS 全放开；如转公网必须先恢复认证与 TLS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "accessMode": settings.ACCESS_MODE}
