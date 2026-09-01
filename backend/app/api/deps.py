"""全局鉴权依赖：所有 /api/v1 接口默认需要登录令牌。

- 令牌来源：Authorization: Bearer <token> 或 ?access_token=<token>（文件下载等浏览器直开场景）
- 豁免路径：登录接口本身、1688 OAuth 回调（外部重定向无法携带请求头）
- 角色校验：require_roles("admin", ...) 供敏感接口做 RBAC
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import decode_token
from app.db import get_db
from app.models.org import User

# 无需登录即可访问（登录本身 + 1688 授权回调跳转）
_PUBLIC_PATHS = {"/api/v1/auth/login"}
_PUBLIC_PREFIXES = ("/api/v1/integrations/alibaba1688/callback",)


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("access_token")


async def require_auth(request: Request, db: Session = Depends(get_db)) -> User | None:
    """api_router 级依赖：校验令牌并加载用户；豁免路径直接放行。"""
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        return None

    token = _extract_token(request)
    payload = decode_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    user = db.get(User, int(payload.get("uid") or 0))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")

    # 后续业务审计可直接取 request.state.username
    request.state.username = user.username
    return user


def require_roles(*role_codes: str):
    """RBAC 依赖工厂：仅允许持有指定角色之一的用户访问。"""

    def checker(user: User = Depends(require_auth)) -> User:
        if user is None:  # 豁免路径上不会挂本依赖，防御性兜底
            raise HTTPException(status_code=401, detail="未登录")
        codes = {r.code for r in user.roles}
        if not codes.intersection(role_codes):
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return checker
