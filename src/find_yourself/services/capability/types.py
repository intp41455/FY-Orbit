"""统一能力网关 · 类型与资源颗粒度 (补齐包1 A-能力网关-01/03/04)。

四维设计里的「全」与「细」在此定义：

* **全**：本机能力（``local.*``）与跨 agent 能力（``cross_agent.*``）共用同一张
  :class:`CapabilityTypeRegistry` 注册表。新能力类型 **仅注册即接入**——
  裁决管线（profile → level → automation → actor/RBAC → grant）对所有类型一视同仁，
  注册方（含包3 的 MCP/A2A/CLI/进程内通道）不需要改任何裁决代码。
* **细**：每个请求携带具体资源（路径 / 域名 / 进程名）与目标执行级别；
  授权颗粒度由 :mod:`find_yourself.services.capability.grants` 的四元组
  （能力 × 资源 × 时限 × 可撤回）进一步收窄。

诚实边界：本模块只做「表达」，不做「裁决」。裁决算法在
:mod:`find_yourself.services.capability.broker`（唯一入口），通道执行体
（包3）在调用外部系统之前必须经 broker 裁决。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

#: 能力域：本机能力 vs 跨 agent/应用能力（03：四类万能接口都挂 cross_agent 域）。
DOMAIN_LOCAL = "local"
DOMAIN_CROSS_AGENT = "cross_agent"

#: 资源颗粒度（04 四元组的第二元）。
RESOURCE_PATH = "path"        # 路径前缀（本机文件触达）
RESOURCE_DOMAIN = "domain"    # 域名白名单（网络 / 跨 agent 端点）
RESOURCE_PROCESS = "process"  # 进程白名单（L3 进程树 / CLI driver）
RESOURCE_LEVEL = "level"      # 以「执行级别」为资源（越级授权专用）
RESOURCE_ALL = "*"            # 不限资源（仅限零风险类型谨慎使用）

RESOURCE_KINDS = (RESOURCE_PATH, RESOURCE_DOMAIN, RESOURCE_PROCESS, RESOURCE_LEVEL, RESOURCE_ALL)

#: 授权效果。deny 显式覆盖一切 allow（多路取最严的一部分）。
EFFECT_ALLOW = "allow"
EFFECT_DENY = "deny"
EFFECTS = (EFFECT_ALLOW, EFFECT_DENY)


# ---------------------------------------------------------------------------
# 能力类型注册表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityTypeSpec:
    """一种能力的静态描述（注册即接入裁决管线）。

    Attributes
    ----------
    name:
        能力名，点分小写，如 ``local.fs.read`` / ``cross_agent.mcp.tool``。
    domain:
        :data:`DOMAIN_LOCAL` 或 :data:`DOMAIN_CROSS_AGENT`。
    resource_kind:
        该能力的资源颗粒度类型（路径 / 域名 / 进程）；``None`` 表示无资源维度。
    default_level:
        本机能力的默认执行级别（``L1``-``L5``）；跨 agent 能力为 ``None``。
    automation_min_mode:
        非 ``None`` 时该能力受 GUI 自动化权限门
        （:mod:`find_yourself.services.automation.permissions`）约束，需该档位以上。
    reversible:
        True 时调用方可为该能力登记回滚回调（06 可回滚可逆动作）。
    """

    name: str
    domain: str
    description: str
    risk_notes: str = ""
    resource_kind: str | None = None
    default_level: str | None = None
    automation_min_mode: str | None = None
    reversible: bool = False

    def matches(self, capability: str) -> bool:
        """能力名匹配：全等、``*`` 通配或前缀通配（``cross_agent.*``）。"""
        if self.name == capability or self.name == "*":
            return True
        if self.name.endswith(".*") and capability.startswith(self.name[:-1]):
            return True
        return False


def _builtin_specs() -> tuple[CapabilityTypeSpec, ...]:
    """内置能力类型清单（02 五级分级 × 03 四类跨 agent 接口）。"""
    return (
        # ---- L1 沙箱内（novice 档零配置基线：默认级别 L1/L2 的本机能力）----
        CapabilityTypeSpec(
            name="local.fs.read", domain=DOMAIN_LOCAL,
            description="读取沙箱/制品目录内文件（L1）",
            risk_notes="仅限沙箱与制品目录；越界由网关路径边界与沙箱双重校验",
            resource_kind=RESOURCE_PATH, default_level="L1",
        ),
        CapabilityTypeSpec(
            name="local.fs.write", domain=DOMAIN_LOCAL,
            description="在沙箱/制品目录内写文件（L1）",
            risk_notes="写入限于沙箱/制品目录；敏感文件名由沙箱另行拒绝",
            resource_kind=RESOURCE_PATH, default_level="L1",
        ),
        CapabilityTypeSpec(
            name="local.sandbox.exec", domain=DOMAIN_LOCAL,
            description="在隔离沙箱内执行脚本（L1）",
            risk_notes="进程级隔离而非安全边界（见 runtime/sandbox.py 诚实声明）",
            resource_kind=None, default_level="L1", reversible=True,
        ),
        # ---- L2 沙箱外工作区 ----
        CapabilityTypeSpec(
            name="local.workspace.read", domain=DOMAIN_LOCAL,
            description="读取工作区文件（L2，沙箱外）",
            risk_notes="触达沙箱外工作区；路径前缀授权必须显式收紧",
            resource_kind=RESOURCE_PATH, default_level="L2",
        ),
        CapabilityTypeSpec(
            name="local.workspace.write", domain=DOMAIN_LOCAL,
            description="写入工作区文件（L2，沙箱外）",
            risk_notes="可修改用户工作区文件；建议细粒度档按路径前缀授予",
            resource_kind=RESOURCE_PATH, default_level="L2", reversible=True,
        ),
        # ---- L3 进程树 ----
        CapabilityTypeSpec(
            name="local.process.spawn", domain=DOMAIN_LOCAL,
            description="派生并控制进程树（L3）",
            risk_notes="可执行任意本机命令；必须进程白名单 + 显式授予",
            resource_kind=RESOURCE_PROCESS, default_level="L3",
        ),
        CapabilityTypeSpec(
            name="cross_agent.cli.exec", domain=DOMAIN_CROSS_AGENT,
            description="以 CLI 驱动方式调用外部 agent/应用（L3）",
            risk_notes="经命令行驱动外部程序；受进程白名单约束",
            resource_kind=RESOURCE_PROCESS, default_level="L3",
        ),
        # ---- L4 桌面自动化 / 无障碍注入 ----
        CapabilityTypeSpec(
            name="local.desktop.observe", domain=DOMAIN_LOCAL,
            description="只读屏幕观察（L4）",
            risk_notes="读取屏幕内容可能涉及隐私；受 automation readonly 档约束",
            resource_kind=None, default_level="L4", automation_min_mode="readonly",
        ),
        CapabilityTypeSpec(
            name="local.desktop.input", domain=DOMAIN_LOCAL,
            description="移动光标（L4）",
            risk_notes="受 automation safe 档约束",
            resource_kind=None, default_level="L4", automation_min_mode="safe",
        ),
        CapabilityTypeSpec(
            name="local.desktop.full", domain=DOMAIN_LOCAL,
            description="点击/打字/按键注入（L4，SYSTEM_FS 级）",
            risk_notes="等同完全控制；full 档强制短 TTL",
            resource_kind=None, default_level="L4", automation_min_mode="full",
        ),
        # ---- L5 驱动级（可选，默认关）----
        CapabilityTypeSpec(
            name="local.driver", domain=DOMAIN_LOCAL,
            description="驱动级触达（L5，可选）",
            risk_notes="最高风险：内核/驱动级注入，默认禁用，须显式授权且逐次审计",
            resource_kind=None, default_level="L5",
        ),
        # ---- 跨 agent 万能接口（03；通道执行体在包3，裁决点在此网关）----
        CapabilityTypeSpec(
            name="cross_agent.mcp.tool", domain=DOMAIN_CROSS_AGENT,
            description="调用 MCP server 工具（MCP host 通道）",
            risk_notes="外部 server 能力；域名/端点白名单 + 显式授予",
            resource_kind=RESOURCE_DOMAIN,
        ),
        CapabilityTypeSpec(
            name="cross_agent.a2a.skill", domain=DOMAIN_CROSS_AGENT,
            description="调用 A2A 远端 agent 技能（A2A client 通道）",
            risk_notes="远端 agent 执行；受域名白名单约束，上游地址见 config.a2a_upstream_url",
            resource_kind=RESOURCE_DOMAIN,
        ),
        CapabilityTypeSpec(
            name="cross_agent.inproc.call", domain=DOMAIN_CROSS_AGENT,
            description="调用进程内插件宿主注册的能力（进程内插件通道）",
            risk_notes="插件与宿主同进程；注册来源必须经技能安全审查",
            resource_kind=None,
        ),
    )


class CapabilityTypeRegistry:
    """能力类型注册表：新类型 **仅注册即接入**，裁决代码零改动。"""

    def __init__(self, specs: Sequence[CapabilityTypeSpec] = ()):
        self._specs: dict[str, CapabilityTypeSpec] = {}
        for spec in specs:
            self._specs[spec.name] = spec

    @classmethod
    def with_builtins(cls) -> "CapabilityTypeRegistry":
        return cls(_builtin_specs())

    def register(self, spec: CapabilityTypeSpec, *, replace: bool = False) -> None:
        """注册一种新能力类型。同名已存在且未给 ``replace`` 时报错（防静默换义）。"""
        if not spec.name or spec.name != spec.name.strip() or any(c.isspace() for c in spec.name):
            raise ValueError(f"invalid capability name: {spec.name!r}")
        if spec.domain not in (DOMAIN_LOCAL, DOMAIN_CROSS_AGENT):
            raise ValueError(f"invalid capability domain: {spec.domain!r}")
        if spec.resource_kind is not None and spec.resource_kind not in RESOURCE_KINDS:
            raise ValueError(f"invalid resource kind: {spec.resource_kind!r}")
        if spec.name in self._specs and not replace:
            raise ValueError(f"capability type already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, capability: str) -> CapabilityTypeSpec | None:
        """按请求能力名取 spec（支持通配注册名匹配）；未知类型返回 None。"""
        if capability in self._specs:
            return self._specs[capability]
        for spec in self._specs.values():
            if spec.name != "*" and spec.matches(capability) and spec.name.endswith(".*"):
                return spec
        return None

    def list(self, domain: str | None = None) -> list[CapabilityTypeSpec]:
        return [s for s in self._specs.values() if domain is None or s.domain == domain]


# ---------------------------------------------------------------------------
# 请求 / 裁决结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRequest:
    """一次能力调用请求（本机动作与跨 agent 调度共用同一表达）。"""

    capability: str
    #: 请求主体（owner id / agent id / service id）。actor 上下文与之冲突时裁决拒绝。
    subject: str = ""
    #: 具体资源：路径 / 域名 / 进程名 / 通道端点。None 表示无资源维度。
    resource: str | None = None
    #: 请求的执行级别（None = 该能力默认级别；高于默认即「越级」）。
    level: str | None = None
    #: 绑定任务（审计可按任务检索；grant 可按任务绑定）。
    task_id: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class Verdict:
    """单路门的裁决。``allowed=None`` 表示弃权（该门对此请求不表态）。"""

    gate: str
    allowed: bool | None
    reason: str = ""
    grant_id: str | None = None


@dataclass(frozen=True)
class Decision:
    """网关的最终裁决（唯一出口的数据形态）。"""

    request: CapabilityRequest
    allowed: bool
    code: str  # allowed / default_deny / denied / path_escape / unknown_capability / subject_mismatch
    reasons: tuple[str, ...] = ()
    verdicts: tuple[Verdict, ...] = ()
    profile: str = ""
    level: str | None = None
    escalated: bool = False
    grant_id: str | None = None


# ---------------------------------------------------------------------------
# 路径边界（A-Claw安全-01：任何级别不得绕过路径越界校验）
# ---------------------------------------------------------------------------


class PathEscape(Exception):
    """请求路径越出该级别允许的根边界（网关侧路径越界校验）。"""


def resolve_path(resource: str, roots: Sequence[Path]) -> Path:
    """把请求资源解析为绝对路径：绝对路径原样 resolve，相对路径相对首个根解析。"""
    raw = Path(resource)
    base = roots[0] if roots else Path.cwd()
    if raw.is_absolute():
        return raw.resolve()
    return (base / raw).resolve()


def ensure_path_within(resource: str, roots: Sequence[Path]) -> Path:
    """A-Claw安全-01 的网关侧防线：路径必须落在 ``roots`` 之一的内部。

    与 ``runtime/local_agents.py::_verify_fs_sandbox``、``runtime/sandbox.py::
    _resolve_within`` 同语义（先 ``resolve()`` 折叠 ``..`` 与符号链接再判包含），
    但作为 **网关前置门** 存在——沙箱自己的校验保留，双重防护，任何级别都不旁路。
    """
    if not roots:
        raise PathEscape(f"no path boundary configured for this level; rejected {resource!r}")
    resolved = resolve_path(resource, roots)
    for root in roots:
        base = Path(root).resolve()
        if resolved == base:
            return resolved
        try:
            resolved.relative_to(base)
            return resolved
        except ValueError:
            continue
    raise PathEscape(
        f"path escapes all allowed roots for this level: {resource!r} -> {resolved}"
    )


def path_matches_prefix(path: str, prefix: str) -> bool:
    """四元组里的路径前缀匹配：按路径段匹配，``/a/b`` 不误匹配 ``/a/bc``。"""
    p = Path(path)
    q = Path(prefix)
    try:
        p.relative_to(q)
        return True
    except ValueError:
        pass
    # 相对前缀：逐段比较（大小写不敏感，Windows 友好）。
    pp = [seg.lower() for seg in p.parts]
    qq = [seg.lower() for seg in q.parts]
    if len(qq) > len(pp):
        return False
    return pp[: len(qq)] == qq


def domain_matches(domain: str, pattern: str) -> bool:
    """域名白名单匹配：全等或 ``*.suffix`` 后缀通配。"""
    d = domain.lower().strip()
    pat = pattern.lower().strip()
    if d == pat or pat == "*":
        return True
    if pat.startswith("*."):
        return d.endswith(pat[1:]) and d != pat[1:]
    return False


def process_matches(name: str, pattern: str) -> bool:
    """进程白名单匹配：全等或尾部通配（``python*``）。"""
    n = name.lower().strip()
    pat = pattern.lower().strip()
    if pat == "*":
        return True
    if pat.endswith("*"):
        return n.startswith(pat[:-1])
    return n == pat
