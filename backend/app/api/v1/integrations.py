from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import integration_service

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("")
def list_integrations(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return integration_service.integration_status(db)


@router.post("/jackyun/test")
def test_jackyun(db: Session = Depends(get_db)) -> dict[str, Any]:
    """真实 MCP 连接测试：initialize → tools/list。结果如实返回。"""
    return integration_service.test_jackyun(db)


# ---------- 1688 OAuth（规格 7.2：先完成授权页面/callback 骨架；未配置如实显示等待） ----------

@router.get("/alibaba1688/auth-url")
def alibaba1688_auth_url() -> dict[str, Any]:
    """生成 1688 授权跳转地址。未配置 AppKey/Secret 时明确报错，不伪造已连接。"""
    from uuid import uuid4

    from app.adapters.alibaba1688 import Alibaba1688Adapter
    from app.adapters.base import AdapterNotConfigured

    try:
        adapter = Alibaba1688Adapter()
        url = adapter.get_authorization_url(state=str(uuid4()))
    except AdapterNotConfigured as exc:
        raise HTTPException(400, str(exc))
    except NotImplementedError as exc:
        raise HTTPException(400, str(exc))
    return {"authorizationUrl": url}


@router.get("/alibaba1688/callback")
def alibaba1688_callback(
    code: str = Query(...),
    state: str = Query(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """1688 授权回调：token 交换需 AppKey/Secret 配置后实现；未配置时记录并如实返回等待状态。"""
    from app.adapters.alibaba1688 import Alibaba1688Adapter
    from app.adapters.base import AdapterNotConfigured
    from app.services.integration_service import ensure_exception

    try:
        adapter = Alibaba1688Adapter()
        result = adapter.handle_callback(code, state)
        return {"ok": True, **result}
    except AdapterNotConfigured as exc:
        ensure_exception(db, "ALIBABA1688_NOT_CONFIGURED", "1688 未配置",
                         f"回调已收到但未配置 AppKey/Secret: {exc}")
        return {"ok": False, "status": "waiting_config",
                "message": "回调已收到；1688 开放平台应用创建并配置 AppKey/Secret 后完成授权"}
    except NotImplementedError as exc:
        return {"ok": False, "status": "token_exchange_pending",
                "message": str(exc) or "token 交换待 1688 应用权限确认后实现"}
