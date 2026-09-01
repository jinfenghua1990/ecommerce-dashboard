from typing import Any

from fastapi import APIRouter, Depends
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
