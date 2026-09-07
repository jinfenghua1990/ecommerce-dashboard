import os

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.deps import require_auth
from app.api.v1 import api_router
from app.config import settings
from app.core.logging import configure_logging

# 前端静态产物（next export 输出）。可通过 FRONTEND_OUT 覆盖。
_FRONTEND_OUT = os.environ.get(
    "FRONTEND_OUT",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "out")),
)


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
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(_request, call_next):
    response = await call_next(_request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    # HTML 每次使用前都向服务器校验新鲜度：next build 会清空旧 chunk，
    # 浏览器缓存的旧 index.html 引用已删除的 chunk 会导致「改完没生效」，必须硬刷新才能好。
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# 全局登录鉴权：除登录/1688回调等豁免路径外，全部 /api/v1 需要令牌（见 api/deps.py）
app.include_router(api_router, dependencies=[Depends(require_auth)])


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "accessMode": settings.ACCESS_MODE}


# ---------- 前端静态托管（next export 产物，单口 8000 同服 API + 前端） ----------
def _resolve_static(rel_path: str) -> str | None:
    """把 clean URL 解析到 out/ 下的真实文件：/products -> products.html / products/index.html。"""
    if not rel_path or rel_path == "/":
        return os.path.join(_FRONTEND_OUT, "index.html")
    cand = [
        os.path.join(_FRONTEND_OUT, rel_path),
        os.path.join(_FRONTEND_OUT, rel_path + ".html"),
        os.path.join(_FRONTEND_OUT, rel_path, "index.html"),
    ]
    for c in cand:
        if os.path.isfile(c):
            return c
    return None


app.mount("/_next", StaticFiles(directory=os.path.join(_FRONTEND_OUT, "_next")), name="next-static")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # /api 与 /healthz 已在上方路由优先匹配，这里只兜底前端资源与 SPA 路由
    if full_path.startswith("api"):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not Found")
    file_path = _resolve_static(full_path)
    if file_path is None:
        # 带扩展名的缺失资源（图片/字体等）直接 404，避免误回 index.html
        if "." in full_path:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not Found")
        # 其余未知路径交给前端路由（SPA fallback）
        file_path = os.path.join(_FRONTEND_OUT, "index.html")
    return FileResponse(file_path)
