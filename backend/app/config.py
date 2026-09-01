from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。所有秘密只从环境变量/.env 读取，禁止进入 Git 与前端。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "电商经营数据平台"
    APP_SECRET_KEY: str = ""
    ACCESS_MODE: str = "lan_trusted"  # lan_trusted | rbac（rbac 时全部 /api/v1 需登录令牌）

    # 初始管理员（仅首次启动创建时生效；改密码后不会被覆盖，除非 FORCE_ADMIN_PASSWORD=1）
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""
    FORCE_ADMIN_PASSWORD: bool = False

    # CORS：默认允许同机 Next dev (3000) + 本平台前端 (18080) + 8888 聚合中心。
    # 聚合中心需跨域读 /healthz 渲染状态点，故纳入白名单。
    # 转公网前必须改成严格白名单并启用 RBAC。
    CORS_ALLOW_ORIGINS: str = (
        "http://localhost:3000,http://localhost:18080,http://127.0.0.1:18080,"
        "http://localhost:8888,http://127.0.0.1:8888"
    )

    # CORS 正则：放行局域网 IP 上的 8888 聚合中心（宿主机 IP 由 DHCP 分配，无法逐个写死）。
    # 仅覆盖 RFC1918 私网段且只匹配 8888 端口；转公网必须置空并改回严格白名单。
    CORS_ALLOW_ORIGIN_REGEX: str = (
        r"^http://(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):8888$"
    )

    DATABASE_URL: str = "postgresql+psycopg://ecommerce:ecommerce@postgres:5432/ecommerce"
    REDIS_URL: str = "redis://redis:6379/0"

    DATA_DIR: str = "/data"
    TZ: str = "Asia/Shanghai"

    # 吉客云（Phase 1）
    JACKYUN_MCP_URL: str = ""
    JACKYUN_APP_KEY: str = ""
    JACKYUN_MCP_TOKEN: str = ""

    # 1688 开放平台（Phase 4，未提供前显示未配置）
    ALIBABA_1688_APP_KEY: str = ""
    ALIBABA_1688_APP_SECRET: str = ""
    ALIBABA_1688_REDIRECT_URI: str = ""

    # 财务邮件（Phase 6）
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    @property
    def jackyun_configured(self) -> bool:
        return bool(self.JACKYUN_MCP_URL and self.JACKYUN_MCP_TOKEN)

    @property
    def alibaba_1688_configured(self) -> bool:
        return bool(self.ALIBABA_1688_APP_KEY and self.ALIBABA_1688_APP_SECRET)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USERNAME and self.SMTP_PASSWORD)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
