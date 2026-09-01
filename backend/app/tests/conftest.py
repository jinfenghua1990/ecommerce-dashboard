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
from sqlalchemy.orm import sessionmaker

from app.db import engine, get_db
from app.main import app


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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
