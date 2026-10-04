"""插件包签名校验 + 自动静态扫描 + 上架门禁策略（需求 14，第一切片）。

用户决策原文：「插件允许任意脚本，但必须符合业界安全规范」。所以本模块的重心
是**安全侧**，市场 UI 留给后续切片。

三件事，各有一段"为什么这样做"
--------------------------------

1. **包签名**（:func:`verify_package_signature` / :func:`sign_package`）
   ----------------------------------------------------------------
   签名用 Ed25519（业界包签名主流，如 sigstore/ed25519），把「这份包是谁发布的」
   钉死在 ``skills.package_hash`` 覆盖的那串**规范化字节**上。验证函数的契约是
   **fail closed**：签名错、公钥错、算法未知、base64 坏、空签名——一律返回
   ``False``，绝不抛泄漏内部细节的异常。篡改包内**任意一个字节**都会改变
   :func:`canonical_package_bytes`，验证必然失败。

   私钥**永不入库**：本模块只接受私钥作为内存参数做签名（供发布方与测试用），
   入库的只有 :class:`~find_yourself.db.plugin_models.PluginSigningKey` 的**公钥**。

2. **自动扫描**（:func:`scan_package`）
   ----------------------------------
   扫描是**真实静态检查**（正则/字面量分析，不执行被扫代码），不是占位符。每条
   发现都带 ``code`` / ``severity`` / ``location``。规则覆盖：
   * 危险 import（``subprocess`` / ``socket`` / ``ctypes`` / ``pickle`` ...）
   * 危险调用（``os.system`` / ``os.popen`` / ``eval(`` / ``exec(`` ...）
   * 破坏性 shell（``rm -rf`` / ``mkfs`` / ``dd if=`` / fork bomb ...）
   * 明文凭据（私钥头 / 已知厂商 key 前缀 / ``FY_*`` / ``SECRET`` / 明文赋值）
   * 路径穿越字面量（``../``）

   **给风险等级而非一刀切**：只有 ``critical`` / ``high`` 才阻断上架
   （:data:`BLOCKING_SEVERITIES`）。示例凭据、占位串、文档里的相对路径归
   ``medium``——它们值得记录，但不该把一份「教人怎么写安全代码」的文档判死。

3. **上架门禁策略**（:data:`PROMOTION_GATE_POLICY` / :func:`evaluate_promotion_gate`）
   ------------------------------------------------------------------------------
   门禁是**服务端策略常量**，不是调用方参数——参照 ``artifact_gate.py`` 的
   ``GATE_POLICY``。调用方**不可能**通过传参声明「本次无需签名 / 无需扫描」。

   策略按**服务端分类**（:func:`classify_package`）给要求：
   * ``instruction``（包内只有内联文本字段，如 ``skill_md`` / ``code``，没有会
     落盘的文件条目）→ 必须过扫描，不强制签名。
   * ``plugin``（含 ``scripts`` / ``files`` 等**文件条目**——映射/列表形态）→
     扫描 + 签名**两者都必须过**。

   威胁模型是「插件允许任意脚本」：真正危险的是会被**物化并执行**的文件，只有
   ``plugin`` 包带这类条目。分类由包结构**服务端判定**，调用方无法传参选择画像；
   且分类**不构成绕过**——扫描对**所有**非惰性内容都跑，内联的危险代码照样会被
   阻断级发现挡下，签名只是对「会落盘执行的文件」额外要求来源证明。
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.plugin_models import SIGNING_KEY_ALGORITHMS, PluginSigningKey
from .actor import Actor
from .errors import ValidationFailed
from .hasher import canonical_json

# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------
#: 默认（也是当前唯一实现的）签名算法。
DEFAULT_SIGNING_ALGORITHM = "ed25519"

#: Ed25519 原始公钥/私钥字节长度。用来在登记公钥时**立刻**发现长度不对的输入，
#: 而不是等到某次验证莫名其妙地失败。
_ED25519_KEY_BYTES = 32


def canonical_package_bytes(package: dict[str, Any]) -> bytes:
    """签名所覆盖的**规范化字节**。

    刻意复用 :func:`find_yourself.services.hasher.canonical_json`——``package_hash``
    就是 ``sha256(canonical_json(package))``。签名与哈希覆盖**同一串字节**，
    于是「哈希对得上」自动蕴含「签名覆盖的就是这份内容」，不会出现两个规范
    化口径漂移导致签名与哈希各说各话。
    """
    return canonical_json(package).encode("utf-8")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def generate_signing_keypair() -> tuple[str, str]:
    """生成一对 Ed25519 密钥，返回 ``(私钥b64, 公钥b64)``。

    只为发布方/测试提供便利。**私钥不进任何持久层**——它是调用方内存里的东西。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    priv_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64encode(priv_raw), _b64encode(pub_raw)


def sign_package(
    *, private_key: str, package: dict[str, Any], algorithm: str = DEFAULT_SIGNING_ALGORITHM
) -> str:
    """用私钥对包签名，返回 base64 签名。私钥只在此内存中流转。"""
    if algorithm != "ed25519":
        raise ValidationFailed("unsupported_algorithm", f"Unsupported signing algorithm {algorithm!r}")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        raw = _b64decode(private_key)
        key = Ed25519PrivateKey.from_private_bytes(raw)
    except (binascii.Error, ValueError) as exc:
        raise ValidationFailed("bad_private_key", "private key is not a valid base64 Ed25519 key") from exc
    return _b64encode(key.sign(canonical_package_bytes(package)))


def verify_package_signature(
    *, package: dict[str, Any], signature: str, public_key: str,
    algorithm: str = DEFAULT_SIGNING_ALGORITHM,
) -> bool:
    """验证包签名。**fail closed**：任何异常/坏输入 → ``False``。

    契约（``tests/unit/test_plugin_signing.py`` 逐条钉住）：
    * 合法签名 + 正确公钥 → ``True``
    * 篡改包内任意一个字节 → ``False``
    * 错误的公钥 → ``False``
    * 空签名 / 非 base64 / 长度不对 → ``False``
    """
    if algorithm not in SIGNING_KEY_ALGORITHMS:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        sig = _b64decode(signature)
        pub = Ed25519PublicKey.from_public_bytes(_b64decode(public_key))
        pub.verify(sig, canonical_package_bytes(package))
        return True
    except (binascii.Error, ValueError, InvalidSignature, TypeError):
        return False
    except Exception:  # noqa: BLE001 - 验证路径必须 fail closed，绝不把异常抛给调用方
        return False


def _validate_public_key(algorithm: str, public_key: str) -> None:
    if algorithm not in SIGNING_KEY_ALGORITHMS:
        raise ValidationFailed(
            "unsupported_algorithm",
            f"algorithm must be one of {list(SIGNING_KEY_ALGORITHMS)}, got {algorithm!r}",
        )
    try:
        raw = _b64decode(public_key)
    except (binascii.Error, ValueError) as exc:
        raise ValidationFailed("bad_public_key", "public_key must be base64") from exc
    expected = _ED25519_KEY_BYTES if algorithm == "ed25519" else None
    if expected is not None and len(raw) != expected:
        raise ValidationFailed(
            "bad_public_key",
            f"{algorithm} public key must decode to {expected} bytes, got {len(raw)}",
        )


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
RISK_LEVELS = ("critical", "high", "medium", "low")

#: 只有这些等级阻断上架。``medium`` / ``low`` 记录但不阻断。
BLOCKING_SEVERITIES = ("critical", "high")

SCANNER_VERSION = "fy-plugin-scan/1"

#: 包内**惰性元数据**键：它们是描述信息，扫描时跳过（不当作可执行内容）。
INERT_PACKAGE_KEYS = frozenset({
    "name", "semantic_version", "version", "skill_md", "source", "license",
    "domain", "description", "title", "author", "tags",
})

#: 这些键下的文本**不被当作可执行内容**（仅元数据），扫描时跳过。
_METADATA_ONLY_KEYS = INERT_PACKAGE_KEYS - {"skill_md"}

#: 危险 import：模块 → (severity, why)。
DANGEROUS_IMPORTS: dict[str, tuple[str, str]] = {
    "subprocess": ("medium", "spawns child processes; needs sandbox isolation"),
    "socket": ("high", "raw network sockets; egress must be isolated"),
    "ctypes": ("high", "arbitrary native memory calls; bypasses Python safety"),
    "pickle": ("medium", "unpickling untrusted data can execute code"),
    "marshal": ("high", "marshal deserialization can execute/segfault"),
    "multiprocessing": ("medium", "spawns processes; needs sandbox isolation"),
    "pty": ("high", "allocates pseudo-terminals for interactive shells"),
}

#: 危险调用用正则匹配（避开 "eval(" 混进 "evaluate(" 这类误报）。
_DANGEROUS_CALL_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\bos\.system\s*\("), "os.system", "runs a shell command"),
    (re.compile(r"\bos\.popen\s*\("), "os.popen", "runs a shell command"),
    (re.compile(r"\bos\.(?:exec|spawn)\w*\s*\("), "os.exec/spawn", "replaces or spawns a process"),
    (re.compile(r"\b__import__\s*\("), "__import__", "dynamic import bypasses static review"),
    (re.compile(r"(?<![\w.])eval\s*\("), "eval", "evaluates arbitrary code"),
    (re.compile(r"(?<![\w.])exec\s*\("), "exec", "executes arbitrary code"),
)

#: 破坏性 shell 字面量。
_DESTRUCTIVE_SHELL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\b"), "rm -r/-rf recursive delete"),
    (re.compile(r"\bmkfs\b"), "mkfs formats a filesystem"),
    (re.compile(r"\bdd\s+if="), "dd raw device write"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:"), "fork bomb"),
    (re.compile(r"\bshutdown\b"), "shuts down the host"),
    (re.compile(r"\bformat\s+[A-Za-z]:"), "formats a Windows drive"),
)

#: 私钥头——出现即 ``critical``。真实私钥绝不该出现在包内容里。
_PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")

#: 已知厂商 key 前缀（真实 key 才有足够长度；占位串如 ``sk-proj-xxxxx`` 不匹配）。
_PROVIDER_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,})\b"
)

#: ``FY_`` 环境变量名 / ``*SECRET*`` 环境变量名（全大写下划线）。
_ENV_SECRET_NAME = re.compile(r"\b(?:FY_[A-Z0-9_]+|[A-Z][A-Z0-9_]*SECRET[A-Z0-9_]*)\b")

#: 明文凭据赋值。
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:secret|password|passwd|token|api[_-]?key|access[_-]?key|private[_-]?key)"
    r"\s*[:=]\s*[\"'][^\"']{4,}[\"']"
)

#: 路径穿越字面量 ``../`` 或 ``..\``。
_PATH_TRAVERSAL = re.compile(r"\.\.[/\\]")


def _finding(code: str, severity: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "location": location, "message": message}


def _iter_texts(location: str, value: Any) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield location, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_texts(f"{location}:{key}", item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_texts(location, item)


def _scannable_texts(package: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """产出 ``(location, text)``。惰性元数据键（name/license/...）不参与扫描。"""
    for key, value in package.items():
        if key in _METADATA_ONLY_KEYS:
            continue
        yield from _iter_texts(key, value)


def _scan_text(location: str, text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    # 危险 import
    for module, (severity, why) in DANGEROUS_IMPORTS.items():
        if re.search(rf"\b(?:import|from)\s+{re.escape(module)}\b", text):
            found.append(_finding("dangerous_import", severity, location, f"imports {module}: {why}"))
    # 危险调用
    for pattern, label, why in _DANGEROUS_CALL_PATTERNS:
        if pattern.search(text):
            found.append(_finding("dangerous_call", "high", location, f"calls {label}: {why}"))
    # 破坏性 shell
    for pattern, label in _DESTRUCTIVE_SHELL_PATTERNS:
        if pattern.search(text):
            found.append(_finding("destructive_shell", "critical", location, f"destructive shell: {label}"))
    # 明文凭据
    if _PRIVATE_KEY_HEADER.search(text):
        found.append(_finding("plaintext_credential", "critical", location, "embedded private key header"))
    if _PROVIDER_SECRET.search(text):
        found.append(_finding("plaintext_credential", "high", location, "looks like a real provider credential"))
    if _ENV_SECRET_NAME.search(text):
        found.append(_finding("plaintext_credential", "medium", location, "references a secret-like env var name"))
    if _CREDENTIAL_ASSIGNMENT.search(text):
        found.append(_finding("plaintext_credential", "medium", location, "hard-coded credential assignment"))
    # 路径穿越：可执行内容里为 high（真的会去读写越界路径），文档里为 medium。
    if _PATH_TRAVERSAL.search(text):
        severity = "medium" if location == "skill_md" else "high"
        found.append(_finding("path_traversal", severity, location, "path traversal literal '../'"))
    return found


def scan_package(package: dict[str, Any]) -> dict[str, Any]:
    """对一个包做静态扫描，返回结构化报告。

    报告字段：``passed`` / ``risk_level`` / ``finding_count`` / ``blocking_count``
    / ``findings[]``（每条含 ``code`` / ``severity`` / ``location`` / ``message``）。
    ``passed`` 为真当且仅当没有任何 ``critical`` / ``high`` 级别的发现。
    """
    raw: list[dict[str, str]] = []
    for location, text in _scannable_texts(package):
        raw.extend(_scan_text(location, text))

    # 去重（同一 code+severity+location+message 只留一条）。
    seen: set[tuple[str, str, str, str]] = set()
    findings: list[dict[str, str]] = []
    for item in raw:
        key = (item["code"], item["severity"], item["location"], item["message"])
        if key in seen:
            continue
        seen.add(key)
        findings.append(item)

    blocking = [f for f in findings if f["severity"] in BLOCKING_SEVERITIES]
    risk = "none"
    for level in RISK_LEVELS:
        if any(f["severity"] == level for f in findings):
            risk = level
            break
    return {
        "scanner_version": SCANNER_VERSION,
        "passed": len(blocking) == 0,
        "risk_level": risk,
        "finding_count": len(findings),
        "blocking_count": len(blocking),
        "blocking_severities": list(BLOCKING_SEVERITIES),
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# 上架门禁策略
# ---------------------------------------------------------------------------
#: 门禁画像：``instruction`` —— 惰性指令包（不执行）；``plugin`` —— 含可执行条目。
GATE_PROFILES = ("instruction", "plugin")

#: **服务端策略**（调用方不可削弱）：每种画像要求哪些门禁。
PROMOTION_GATE_POLICY: dict[str, dict[str, bool]] = {
    "instruction": {"require_scan": True, "require_signature": False},
    "plugin": {"require_scan": True, "require_signature": True},
}


def classify_package(package: dict[str, Any]) -> str:
    """按**包结构**判定门禁画像。

    判据是「包是否带了**会落盘的文件条目**」：任何一个值是映射/列表（即一份
    ``{文件名: 内容}`` 或文件清单）→ ``plugin``；否则（仅内联文本字段，如
    ``skill_md`` / ``code``）→ ``instruction``。

    为什么以「文件条目」为界而不是「任意非惰性键」：威胁模型是「插件允许任意
    **脚本**」——真正危险的是会被**物化并执行**的文件；而一个内联字符串字段
    （哪怕是 ``code``）没有任何已接线的运行时会去执行它。且这一点**不构成绕过**：
    扫描对**所有**非惰性内容都跑（:func:`_scannable_texts`），内联的危险代码
    照样会以``critical``/``high`` 发现把上架挡下——签名只是对「会落盘执行的
    文件」额外要求来源证明。调用方无法传参选择画像。
    """
    for value in package.values():
        if isinstance(value, (dict, list, tuple)):
            return "plugin"
    return "instruction"


def evaluate_promotion_gate(
    *, profile: str, scan_passed: bool, signature_verified: bool
) -> dict[str, Any]:
    """按服务端策略判定「这份包能否上架」，并给出**为什么**。

    返回 ``{"allowed": bool, "profile": str, "required": {...}, "reasons": [...]}``。
    未知画像一律拒绝（fail closed），没有「默认放行」。
    """
    policy = PROMOTION_GATE_POLICY.get(profile)
    if policy is None:
        return {
            "allowed": False,
            "profile": profile,
            "required": {"require_scan": True, "require_signature": True},
            "reasons": [f"unknown_gate_profile:{profile}"],
        }
    reasons: list[str] = []
    if policy["require_scan"] and not scan_passed:
        reasons.append("scan_not_passed")
    if policy["require_signature"] and not signature_verified:
        reasons.append("signature_not_verified")
    return {
        "allowed": not reasons,
        "profile": profile,
        "required": dict(policy),
        "reasons": reasons,
    }


def lookup_signing_key(session: Session, key_id: str | None) -> PluginSigningKey | None:
    if not key_id:
        return None
    return session.execute(
        select(PluginSigningKey).where(PluginSigningKey.id == key_id)
    ).scalar_one_or_none()


class SigningKeyService:
    """登记 / 吊销插件签名**公钥**。私钥永不经此入库。"""

    def __init__(self, session: Session, audit: Any | None = None):
        self.session = session
        self.audit = audit

    def register(
        self, actor: Actor, *, key_id: str, public_key: str,
        algorithm: str = DEFAULT_SIGNING_ALGORITHM, label: str = "",
    ) -> PluginSigningKey:
        actor.require_authenticated()
        if not (key_id or "").strip():
            raise ValidationFailed("key_id_required", "key_id is required")
        _validate_public_key(algorithm, public_key)
        existing = self.session.get(PluginSigningKey, key_id)
        if existing is not None:
            raise ValidationFailed("key_exists", f"signing key {key_id!r} already registered")
        row = PluginSigningKey(
            id=key_id, owner_id=actor.owner_id or actor.service_id,
            algorithm=algorithm, public_key=public_key, label=label or "", state="active",
        )
        self.session.add(row)
        self.session.flush()
        # 审计只落 key_id / 算法，绝不落公钥原值以外的任何密钥材料（更无签名）。
        if self.audit is not None:
            self.audit.append(actor, "plugin.signing_key_registered", key_id,
                              {"algorithm": algorithm, "label": label or ""})
        return row

    def revoke(self, actor: Actor, key_id: str) -> PluginSigningKey:
        actor.require_owner()
        row = self.session.get(PluginSigningKey, key_id)
        if row is None:
            raise ValidationFailed("key_not_found", f"signing key {key_id!r} not found")
        from ..db.types import utcnow

        row.state = "revoked"
        row.revoked_at = utcnow()
        self.session.flush()
        if self.audit is not None:
            self.audit.append(actor, "plugin.signing_key_revoked", key_id, {})
        return row
