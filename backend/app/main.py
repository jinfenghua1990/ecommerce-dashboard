from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_auth
from app.api.v1 import api_router
from app.config import settings
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    yield


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

# CORS：放行 settings.CORS_ALLOW_ORIGINS 显式白名单 + CORS_ALLOW_ORIGIN_REGEX 匹配的源
# （正则用于局域网 IP 上的 8888 聚合中心，DHCP 下 IP 会变，无法写死）。
# 关闭 credentials；转公网必须把正则置空并收紧为严格白名单。
_origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX or None,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局登录鉴权：除登录/1688回调等豁免路径外，全部 /api/v1 需要令牌（见 api/deps.py）
app.include_router(api_router, dependencies=[Depends(require_auth)])


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "accessMode": settings.ACCESS_MODE}
