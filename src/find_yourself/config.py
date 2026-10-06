from functools import lru_cache
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FY_", env_file=".env", extra="ignore")
    environment: str = "local"
    database_url: str = "sqlite:///.runtime/find-yourself.db"
    owner_id: str = "owner"
    local_token: str = ""
    session_secret: str = ""
    public_url: str = "http://127.0.0.1:8000"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_owner_sub: str = ""
    temporal_address: str = ""
    temporal_namespace: str = "default"
    temporal_queue: str = "find-yourself"
    model_api_key: str = ""
    model_base_url: str = ""
    model_name: str = "gpt-4o-mini"
    # ---- W4 模型网关多 Provider（追加字段；FY_MODEL_API_KEY / FY_MODEL_BASE_URL /
    # FY_MODEL_NAME 的既有语义不变）----
    # openai_compat | ollama | anthropic。留空时按旧配置推断（默认 openai_compat；
    # 无 key 且 base_url 指向 Ollama 默认端口时推断为 ollama）。
    model_provider: str = ""
    # 降级链，JSON 数组，例如 [{"provider":"ollama","model":"qwen2.5:7b"}]。
    # 每项可带 provider/model/base_url/api_key，缺省继承主配置。
    model_fallbacks: list[dict[str, Any]] = []
    # 未知模型按配置价：{"model-id": {"input_usd_per_1k":"0.001",
    # "output_usd_per_1k":"0.002", "context_window":8192}}
    model_price_overrides: dict[str, dict[str, Any]] = {}
    s3_endpoint: str = ""
    s3_bucket: str = "find-yourself"
    s3_region: str = "us-east-1"
    s3_access_key: str = "fy-minio"
    s3_secret_key: str = "minio_dev_change_me_not_for_prod"
    artifacts_path: str = ".runtime/artifacts"
    otlp_endpoint: str = ""
    agent_endpoints: dict[str, str] = {}
    # MCP ecosystem integration: {server_key: {"command": [...], "env": {...}}}
    # (stdio subprocess transport, matching McpClient.from_subprocess). Empty
    # default = MCP disabled; boot behaviour is unchanged.
    mcp_servers: dict[str, dict[str, Any]] = {}
    # ---- W9 多模态与个人资产库（追加字段）----
    # 个人资产（图片/音频/音乐/文档）的本地磁盘根目录。DB 只存相对
    # storage_path，绝对路径永不出库、永不回前端（由 /api/assets/{id}/raw 代理）。
    assets_dir: str = ".runtime/assets"
    # 图片生成的显式单价（美元/张）。留空时非本机端点一律拒绝调用
    # —— 冻结契约 §7：单价未知不得按零费用放行。
    image_price_usd: str = ""

    # ---- 统一能力网关（补齐包1 A-能力网关-01~06）----
    # 授权档位：novice（小白友好档，粗粒度零配置，默认）| fine（细粒度档，
    # 按「能力×资源×时限×可撤回」四元组细配）。切档即时生效并入审计哈希链。
    capability_profile: str = "novice"
    # 五级触达的级别开关覆盖（默认 L1/L2 开、L3/L4/L5 关）：
    # {"L3": true, "L4": false, ...}，未列出的级别用内置默认。
    # 运行期开关与一键降级由网关（services/capability/levels.py）持久化在
    # .runtime/capability_levels.json，本配置只定初值。
    capability_levels: dict[str, bool] = {}
    # 补齐包3 使用：A2A client 通道的上游服务地址（None = 未配置）。
    # 包3 的跨 agent 调度在执行前必须经能力网关（capability broker）裁决。
    a2a_upstream_url: str | None = None

    # ---- T6 抗中断与断点续作（红线：暂存必须落库，不许内存态）----
    # G1 修复：LangGraph 检查点的落盘位置（sqlite 单文件，重启后仍在）。
    # 可用环境变量 FY_CHECKPOINT_DB 覆盖；空值回退到本默认路径。
    checkpoint_db_path: str = ".runtime/checkpoints/langgraph.sqlite"
    # G6 修复：高危写操作前置快照的落盘根目录。
    snapshots_path: str = ".runtime/snapshots"
    # G5 修复：进程启动时是否自动扫描 open 中断并续作 auto 策略事件
    # （陛下要求「重接网络/重置 API 后自动找到并继续开工」）。
    # confirm/manual 策略的事件永不自动续作；FY_RECOVERY_AUTORESUME=0 可整体关闭。
    recovery_autoresume: bool = True

    @model_validator(mode="after")
    def validate_security(self):
        if self.environment not in {"local", "production", "test"}:
            raise ValueError("Invalid FY_ENVIRONMENT")
        if len(self.session_secret) < 32:
            raise ValueError("FY_SESSION_SECRET must contain at least 32 characters")
        if self.environment == "production":
            if not all([self.oidc_issuer, self.oidc_client_id, self.oidc_owner_sub]):
                raise ValueError("Production requires OIDC and an exact owner subject")
            if not self.public_url.startswith("https://") or self.local_token:
                raise ValueError("Production requires HTTPS and disables local tokens")
            if not self.database_url.startswith("postgresql") or not self.temporal_address:
                raise ValueError("Production requires PostgreSQL and Temporal")
            if not self.s3_endpoint:
                raise ValueError("Production requires private object storage")
        return self


@lru_cache
def _get_settings() -> Settings:
    return Settings()


def settings(refresh: bool = False) -> Settings:
    """获取应用配置单例。

    支持热重载（refresh=True），在测试重置环境变量或配置更新时触发重新实例化与安全校验。
    validate_security() 会在每次实例化时强校验，确保生产安全规则不被绕过。
    """
    if refresh:
        _get_settings.cache_clear()
    return _get_settings()


# 保持与原有 lru_cache 装饰器方法向后兼容
settings.cache_clear = _get_settings.cache_clear  # type: ignore[attr-defined]


def reload_settings() -> Settings:
    """强制重新载入并安全校验配置。"""
    _get_settings.cache_clear()
    return _get_settings()
