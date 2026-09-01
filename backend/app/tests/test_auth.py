"""登录鉴权测试：口令散列 / 令牌签验 / 登录流程 / 未授权 401 / RBAC。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.auth import create_token, decode_token, hash_password, verify_password
from app.main import app
from app.models.org import AuditLog, Role, User, UserRole


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret-密码")
    assert hashed != "s3cret-密码"
    assert verify_password("s3cret-密码", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("s3cret-密码", None)


def test_token_roundtrip():
    token = create_token(uid=42, username="alice")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["uid"] == 42


def test_token_tampered_rejected():
    token = create_token(uid=42, username="alice")
    assert decode_token(token[:-2] + "xx") is None
    assert decode_token("") is None
    assert decode_token("not-a-token") is None


def test_protected_endpoint_requires_token(db_session):
    """无令牌访问受保护接口必须 401。"""
    with TestClient(app) as anon:
        r = anon.get("/api/v1/system/health")
        assert r.status_code == 401
        r = anon.get("/api/v1/purchase/orders")
        assert r.status_code == 401


def test_healthz_public(db_session):
    """健康检查保持公开（容器探针依赖）。"""
    with TestClient(app) as anon:
        r = anon.get("/healthz")
        assert r.status_code == 200


def test_login_success_and_me(client, db_session):
    user = User(
        username="alice",
        display_name="爱丽丝",
        hashed_password=hash_password("alice-pass-123"),
    )
    db_session.add(user)
    db_session.flush()
    role = db_session.scalar(select(Role).where(Role.code == "admin"))
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.flush()

    r = client.post("/api/v1/auth/login", json={"username": "alice", "password": "alice-pass-123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accessToken"]
    assert body["user"]["username"] == "alice"
    assert "admin" in body["user"]["roles"]

    # 新令牌可用
    r2 = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['accessToken']}"}
    )
    assert r2.status_code == 200
    assert r2.json()["username"] == "alice"

    # 清理（login 的 audit 已 commit，需显式删除避免脏数据）
    db_session.execute(delete(AuditLog).where(AuditLog.actor == "alice"))
    db_session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()


def test_login_wrong_password(client, db_session):
    user = User(username="bob", display_name="", hashed_password=hash_password("bob-pass-123"))
    db_session.add(user)
    db_session.flush()

    r = client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong"})
    assert r.status_code == 401

    db_session.execute(delete(AuditLog).where(AuditLog.actor == "bob"))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()


def test_change_password(client, db_session):
    user = User(username="carol", display_name="", hashed_password=hash_password("old-pass-123"))
    db_session.add(user)
    db_session.flush()
    token = create_token(uid=user.id, username="carol")
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "wrong-old", "new_password": "new-pass-456"},
        headers=headers,
    )
    assert r.status_code == 400

    r = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "old-pass-123", "new_password": "new-pass-456"},
        headers=headers,
    )
    assert r.status_code == 200

    db_session.expire_all()
    fresh = db_session.get(User, user.id)
    assert verify_password("new-pass-456", fresh.hashed_password)

    db_session.execute(delete(AuditLog).where(AuditLog.actor == "carol"))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()
