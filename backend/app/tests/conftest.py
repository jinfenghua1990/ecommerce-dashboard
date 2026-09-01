"""共享 pytest fixtures。

TestClient + FastAPI dependency override 模式：用真实 PG（schema 已迁移），
通过 connection-bound session + transaction rollback 实现"无副作用测试"。

不应在测试中写入会被外部接口观察到的脏数据：
- rollback 关 connection，外部 PG 连接看不到；
- 多并发测试各自绑定独立 connection，互不可见；
- TestClient 内部走 ASGI，无网络层。
"""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_token, hash_password
from app.db import engine, get_db
from app.main import app
from app.models.org import AuditLog, Role, User, UserRole


@pytest.fixture(scope="function")
def db_session() -> Generator:
    """每个测试独占一个 connection + transaction，session 绑该 connection。

    测试结束自动 rollback，DB 状态回到测试起点。
    """
    connection = engine.connect()
    transaction = connection.begin()
    SessionTesting = sessionmaker(
        bind=connection, autoflush=False, expire_on_commit=False
    )
    session = SessionTesting()
    try:
        yield session
    finally:
        session.close()
        try:
            transaction.rollback()
        except Exception:
            # transaction 可能因业务异常已被回滚或已 commit，吞掉避免污染 teardown
            pass
        connection.close()


@pytest.fixture(scope="function")
def client(db_session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient + 用 db_session 覆盖 get_db 依赖。"""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    # 全局鉴权生效：为测试客户端准备已登录管理员（flush 不 commit，随事务回滚）
    role = db_session.scalar(select(Role).where(Role.code == "admin"))
    if role is None:
        role = Role(code="admin", name="管理员")
        db_session.add(role)
        db_session.flush()
    user = User(
        username="pytest-admin",
        display_name="测试管理员",
        hashed_password=hash_password("pytest-pass-123"),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.flush()
    token = create_token(uid=user.id, username=user.username)

    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        c._pytest_user = user  # 供个别测试引用
        yield c
    app.dependency_overrides.clear()

    # 业务代码 commit 会绕过事务回滚，这里显式清理测试用户及其痕迹
    try:
        db_session.execute(delete(UserRole).where(UserRole.user_id == user.id))
        db_session.execute(delete(AuditLog).where(AuditLog.actor == user.username))
        db_session.execute(delete(User).where(User.id == user.id))
        db_session.commit()
    except Exception:
        pass
