"""FastAPI application factory (FROZEN_CONTRACT §5).

Builds the application with:

* unified error envelope (no stack/secret leakage),
* server-resolved identity and CSRF/Origin enforcement (see ``deps``),
* no wide-open CORS — the API is same-origin by default; CORS is only added for
  explicitly configured origins, never ``*``.

The factory accepts an injectable ``session_maker`` and ``settings`` so tests can
run on an isolated in-memory SQLite database without touching env/network.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings
from ..config import settings as load_settings
from ..db.session import engine_from_url, session_factory
from ..runtime.temporal import TemporalRuntime
from .errors import register_exception_handlers
from .oidc import OIDCClient
from .routes import api_router

logger = logging.getLogger(__name__)


def build_oidc(settings: Settings) -> OIDCClient | None:
    """Construct the production OIDC client only when fully configured.

    In local/test without OIDC settings, returns None; the local dev-token path
    (loopback only) is used instead. Production settings validation already
    forces OIDC presence, so None here never means an insecure production.
    """
    if not (settings.oidc_issuer and settings.oidc_client_id and settings.oidc_owner_sub):
        return None
    return OIDCClient(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        owner_sub=settings.oidc_owner_sub,
        client_secret=settings.oidc_client_secret,
    )


def create_app(*, session_maker=None, settings: Settings | None = None,
               oidc: OIDCClient | None = None,
               temporal: TemporalRuntime | None = None) -> FastAPI:
    settings = settings or load_settings()

    if session_maker is None:
        engine = engine_from_url(settings.database_url)
        # 只有**自己建**的引擎才归我关。调用方注入 session_maker 时
        # （测试、CLI、嵌入部署）引擎所有权在对方手里，代dispose 会毁掉
        # 别人的连接池。实测：不加这个标记时，单跑一次 create_app() 就会
        # 留下 `ResourceWarning: unclosed database`。
        owns_engine = True
        if engine.dialect.name == "sqlite":
            import find_yourself.db.artifact_gate_models  # noqa: F401  (需求7 产物版本门禁)
            import find_yourself.db.canvas_models  # noqa: F401
            import find_yourself.db.claim_models  # noqa: F401  (P5 共享任务板 task_claims)
            import find_yourself.db.claw_models  # noqa: F401  (Claw 治理域：把关/冲突/事实基线)
            import find_yourself.db.collaboration_models  # noqa: F401  (需求15 评论/@人/通知/角色)
            import find_yourself.db.fork_models  # noqa: F401  (P4 存档分叉 archive_forks)
            import find_yourself.db.hitl_models  # noqa: F401  (需求12 HITL 执行中断)
            import find_yourself.db.kb_models  # noqa: F401  (W3 本地知识库 kb_documents/kb_chunks)
            import find_yourself.db.models  # noqa: F401
            import find_yourself.db.profile_models  # noqa: F401
            import find_yourself.db.prompt_models  # noqa: F401  (P1-06 prompt template library)
            import find_yourself.db.resilience_models  # noqa: F401  (T6 抗中断台账+流式落盘)
            import find_yourself.db.review_models  # noqa: F401  (P9 点哪评哪评审意见)
            import find_yourself.db.session_state_models  # noqa: F401  (P1-21 session-state snapshots)
            import find_yourself.db.staging_models  # noqa: F401  (P1-04 work stash)
            import find_yourself.db.sync_models  # noqa: F401
            import find_yourself.db.team_approval_models  # noqa: F401  (需求6 团队级审批)
            import find_yourself.db.team_models  # noqa: F401
            import find_yourself.db.workbench_models  # noqa: F401
            import find_yourself.services.assets  # noqa: F401  (W9 个人资产库 assets 表)

            from ..db.base import Base
            Base.metadata.create_all(engine)
        session_maker = session_factory(engine)
    else:
        owns_engine = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail closed on dangling tool references before serving traffic.
        from ..skills.harness import attach_tool_consistency_guard

        attach_tool_consistency_guard(app.state.session_maker)
        # W10-B: register the GUI-automation tools onto the governed gateway.
        # They default to off; this only makes the tools exist (permission-gated).
        from ..services.automation.registry import wire_automation_tools
        wire_automation_tools()
        # E1/E2: Auto-discover bundled skill packages and prompt templates.
        from ..skills.discovery import discover_and_create_prompts, discover_and_stage_skills

        await asyncio.to_thread(discover_and_stage_skills, session_maker=app.state.session_maker)
        await asyncio.to_thread(discover_and_create_prompts, session_maker=app.state.session_maker)
        # P2 MCP integration: wire configured MCP servers (FY_MCP_SERVERS) into
        # the dynamic tool registry. Unreachable servers are logged and skipped;
        # the empty default config makes this a no-op. Off the event loop so a
        # slow stdio handshake cannot block boot.
        from ..adapters.mcp import assemble_mcp_tools

        app.state.mcp_status = await asyncio.to_thread(
            assemble_mcp_tools, settings=settings
        )
        tr = temporal if temporal is not None else TemporalRuntime.disabled()
        app.state.temporal = tr
        if settings.temporal_address:
            try:
                await tr.connect(settings)
            except Exception:
                # Never block boot on a flaky Temporal; ready reports it down.
                app.state.temporal = TemporalRuntime.disabled()
        # T6-E/G5：启动扫描——对 auto 策略且供应商指纹一致的 open 中断自动续作
        # （「重接网络/重置 API 后自动找到并继续开工」）。confirm/manual 永不自动续。
        # 任何失败都不阻断启动，结果如实挂到 app.state.recovery_summary。
        try:
            from ..services.recovery import startup_autoresume

            app.state.recovery_summary = await asyncio.to_thread(
                startup_autoresume, session_maker, settings
            )
        except Exception as exc:  # noqa: BLE001 —— 启动韧性：扫描失败不拖垮 boot
            app.state.recovery_summary = {
                "enabled": True, "error": f"{type(exc).__name__}: {exc}"[:300],
            }
        # 调度器周期回收 + 状态桥：此前 reclaim_stale()/subscribe() 只有测试调，
        # 生产零调用者 —— 超时任务永远占着并发名额，用户在总控那头也看不到进展。
        # 桥接失败不阻断 boot：回收是运维兜底，不是启动前置条件。
        try:
            from ..runtime.agent_bus import bus as agent_bus
            from ..runtime.scheduler_bridge import SchedulerBusBridge
            from ..services.scheduler.core import scheduler

            app.state.scheduler_bridge = SchedulerBusBridge.attach(
                scheduler, agent_bus, interval=settings.scheduler_reclaim_interval,
            )
        except Exception as exc:  # noqa: BLE001
            app.state.scheduler_bridge = None
            logger.warning("scheduler bus bridge not attached: %s", exc, exc_info=True)
        yield
        bridge = getattr(app.state, "scheduler_bridge", None)
        if bridge is not None:
            try:
                bridge.close()
            except Exception:  # noqa: BLE001 —— 关停韧性：桥接清理失败不阻断
                logger.warning("scheduler bus bridge close failed", exc_info=True)
        try:
            await app.state.temporal.close()
        except Exception:
            pass
        # 只关自己建的引擎（见上文 owns_engine 的注释）。
        # 放在 lifespan 收尾：进程被强杀时到不了这里，但正常退出/热重载
        # 能把连接池干净还回 OS。dispose 本身不抛，仍兜一层以免关停被它带崩。
        if owns_engine:
            try:
                engine.dispose()
            except Exception:  # noqa: BLE001
                logger.warning("engine dispose failed", exc_info=True)

    app = FastAPI(
        title="Find Yourself API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.session_maker = session_maker
    app.state.oidc = oidc if oidc is not None else build_oidc(settings)
    app.state.temporal = temporal if temporal is not None else TemporalRuntime.disabled()

    # CORS: same-origin default. Only add restrictive CORS for explicitly allowed
    # origins; never allow_credentials=True with allow_origins=["*"].
    allowed = getattr(settings, "cors_allow_origins", None)
    if allowed:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(allowed), allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["content-type", "x-csrf-token", "authorization"],
        )

    register_exception_handlers(app)
    app.include_router(api_router)

    # Optional static directory mount for desktop standalone / unified mode
    static_dir = os.environ.get("FY_STATIC_DIR")
    if not static_dir:
        repo_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
        if repo_dist.is_dir() and (repo_dist / "index.html").is_file():
            static_dir = str(repo_dist)

    if static_dir and Path(static_dir).is_dir():
        from starlette.responses import FileResponse, PlainTextResponse
        from starlette.staticfiles import StaticFiles

        s_path = Path(static_dir).resolve()

        @app.middleware("http")
        async def spa_fallback_middleware(request, call_next):
            response = await call_next(request)
            if response.status_code == 404 and request.method == "GET":
                path = request.url.path
                if not any(path.startswith(prefix) for prefix in ("/api", "/auth", "/health", "/metrics", "/docs", "/openapi")):
                    index_file = s_path / "index.html"
                    if index_file.is_file():
                        return FileResponse(index_file)
            return response

        class _SpaStaticFiles(StaticFiles):
            """未匹配路径的静态兜底。

            直接挂载 ``StaticFiles`` 时，未匹配任何路由的 POST/PUT/DELETE 会
            得到 405（Mount 命中了路径，但静态文件服务只接受 GET/HEAD），
            这会把「端点不存在」误报成「方法不允许」。这里统一按 404 处理，
            让真实的 404 语义透出。
            """

            async def __call__(self, scope, receive, send):
                if scope.get("type") == "http" and scope.get("method") not in ("GET", "HEAD"):
                    response = PlainTextResponse("Not Found", status_code=404)
                    await response(scope, receive, send)
                    return
                await super().__call__(scope, receive, send)

        app.mount("/", _SpaStaticFiles(directory=str(s_path), html=True), name="static")

    return app
