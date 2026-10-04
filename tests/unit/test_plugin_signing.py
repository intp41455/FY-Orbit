"""插件包生态（需求 14 第一切片）：签名 / 自动扫描 / 上架门禁。

覆盖四组：

1. **签名** —— 合法通过；篡改包内**任意一个字节**失败；错公钥 / 空签名 / 坏
   base64 / 未知算法 / 跨包签名一律失败（fail closed）。
2. **扫描** —— 每条规则至少一正一反；报告结构（code/severity/location）断言；
   只有 critical/high 阻断，示例凭据与文档相对路径不阻断。
3. **上架门禁** —— 未签名 plugin 被拒 / 扫描未过被拒 / 两者齐备才通过；
   调用方**无法**用参数削弱门禁（去服务端策略 → 本组用例变红）。
4. **私钥** —— 结构性不入库；审计不含密钥/签名原文；迁移可升可降。

**变异判据**（本文件对实现的最小改动敏感）：
* 去掉 ``verify_package_signature`` 里的真实验证 → 篡改用例变红。
* 把 ``PROMOTION_GATE_POLICY`` 改成调用方可选 → 门禁用例变红。
"""

from __future__ import annotations

import base64
import gc
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

import find_yourself.db.plugin_models  # noqa: F401  (把 plugin_signing_keys 注册到 metadata)
from find_yourself.db.models import Skill
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, ValidationFailed
from find_yourself.services.plugin_signing import (
    BLOCKING_SEVERITIES,
    PROMOTION_GATE_POLICY,
    SigningKeyService,
    classify_package,
    evaluate_promotion_gate,
    generate_signing_keypair,
    scan_package,
    sign_package,
    verify_package_signature,
)
from find_yourself.services.skill import SkillService

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _plugin_package(**extra) -> dict:
    pkg = {
        "name": "demo-plugin",
        "semantic_version": "1.0.0",
        "skill_md": "# demo\n\nA demo plugin.",
        "scripts": {"main.py": "print('hello')\n"},
        "source": "internal",
        "license": "MIT",
        "domain": "personal",
    }
    pkg.update(extra)
    return pkg


def _instruction_package(**extra) -> dict:
    pkg = {
        "name": "demo-instruction",
        "semantic_version": "1.0.0",
        "skill_md": "# demo\n\nJust instructions.",
        "source": "internal",
        "license": "MIT",
        "domain": "personal",
    }
    pkg.update(extra)
    return pkg


def _stage_and_pass(svc: SkillService, actor, package: dict, name: str, **stage_kw) -> tuple:
    skill = svc.stage(
        actor, name=name, semantic_version="1.0.0", package=package,
        source="internal", license_="MIT", domain="personal", **stage_kw,
    )
    ev = svc.evaluate(actor, skill.id, static_passed=True, functional_passed=True)
    return skill, ev


# ===========================================================================
# 1) 签名
# ===========================================================================
def test_valid_signature_verifies():
    priv, pub = generate_signing_keypair()
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    assert verify_package_signature(package=pkg, signature=sig, public_key=pub) is True


def test_tamper_one_byte_fails():
    priv, pub = generate_signing_keypair()
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    assert verify_package_signature(package=pkg, signature=sig, public_key=pub) is True
    # 翻转 skill_md 里的一个字符 —— 等价于篡改一个字节。
    tampered = dict(pkg)
    tampered["skill_md"] = pkg["skill_md"][:-1] + "X"
    assert verify_package_signature(package=tampered, signature=sig, public_key=pub) is False


def test_tamper_in_script_byte_fails():
    priv, pub = generate_signing_keypair()
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    tampered = _plugin_package(scripts={"main.py": "print('HELLO')\n"})
    assert verify_package_signature(package=tampered, signature=sig, public_key=pub) is False


def test_wrong_public_key_fails():
    priv, _pub = generate_signing_keypair()
    _other_priv, other_pub = generate_signing_keypair()
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    assert verify_package_signature(package=pkg, signature=sig, public_key=other_pub) is False


def test_empty_signature_fails():
    _priv, pub = generate_signing_keypair()
    assert verify_package_signature(package=_plugin_package(), signature="", public_key=pub) is False


def test_non_base64_signature_fails():
    _priv, pub = generate_signing_keypair()
    assert verify_package_signature(package=_plugin_package(), signature="!!!not-base64!!!", public_key=pub) is False


def test_unknown_algorithm_fails_closed():
    priv, pub = generate_signing_keypair()
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    assert verify_package_signature(
        package=pkg, signature=sig, public_key=pub, algorithm="rsa-sha1"
    ) is False


def test_signature_for_other_package_fails():
    priv, pub = generate_signing_keypair()
    sig = sign_package(private_key=priv, package=_plugin_package())
    assert verify_package_signature(
        package=_instruction_package(), signature=sig, public_key=pub
    ) is False


def test_signature_is_base64_and_not_the_private_key():
    priv, pub = generate_signing_keypair()
    sig = sign_package(private_key=priv, package=_plugin_package())
    # 签名可以解成 base64，且不等于私钥本身。
    assert base64.b64decode(sig, validate=True)
    assert sig != priv


# ===========================================================================
# 2) 扫描
# ===========================================================================
def test_scan_clean_package_passes():
    report = scan_package(_instruction_package())
    assert report["passed"] is True
    assert report["finding_count"] == 0
    assert report["risk_level"] == "none"


def test_scan_rule_dangerous_import_positive_and_negative():
    bad = scan_package(_plugin_package(scripts={"a.py": "import subprocess\n"}))
    assert any(f["code"] == "dangerous_import" for f in bad["findings"])
    good = scan_package(_plugin_package(scripts={"a.py": "import json\n"}))
    assert not any(f["code"] == "dangerous_import" for f in good["findings"])


def test_scan_high_import_blocks_subprocess_is_non_blocking():
    # subprocess 是 medium（需沙箱，但不阻断）；socket 是 high（阻断）。
    sub = scan_package(_plugin_package(scripts={"a.py": "import subprocess\n"}))
    assert sub["passed"] is True
    sock = scan_package(_plugin_package(scripts={"a.py": "import socket\n"}))
    assert sock["passed"] is False
    assert any(f["severity"] == "high" for f in sock["findings"])


def test_scan_rule_dangerous_call_os_system_blocks():
    report = scan_package(_plugin_package(scripts={"a.py": "os.system('ls')\n"}))
    assert report["passed"] is False
    assert any(f["code"] == "dangerous_call" and f["severity"] == "high" for f in report["findings"])


def test_scan_rule_eval_not_confused_with_evaluate():
    bad = scan_package(_plugin_package(scripts={"a.py": "eval(user_input)\n"}))
    assert any(f["code"] == "dangerous_call" for f in bad["findings"])
    good = scan_package(_plugin_package(scripts={"a.py": "def evaluate(x): return x\n"}))
    assert not any(f["code"] == "dangerous_call" for f in good["findings"])


def test_scan_rule_destructive_shell_blocks():
    report = scan_package(_plugin_package(scripts={"a.sh": "rm -rf /\n"}))
    assert report["passed"] is False
    assert any(f["code"] == "destructive_shell" and f["severity"] == "critical" for f in report["findings"])


def test_scan_rule_path_traversal_in_script_blocks():
    report = scan_package(_plugin_package(scripts={"a.py": "open('../../etc/passwd')\n"}))
    assert report["passed"] is False
    assert any(f["code"] == "path_traversal" and f["severity"] == "high" for f in report["findings"])


def test_scan_rule_path_traversal_in_skill_md_is_non_blocking():
    report = scan_package(_instruction_package(skill_md="See ../../docs/guide.md for details."))
    assert any(f["code"] == "path_traversal" for f in report["findings"])
    assert report["passed"] is True  # 文档里的相对路径不阻断


def test_scan_rule_private_key_header_blocks():
    pem = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"
    report = scan_package(_plugin_package(scripts={"k.txt": pem}))
    assert report["passed"] is False
    assert any(f["code"] == "plaintext_credential" and f["severity"] == "critical" for f in report["findings"])


def test_scan_rule_example_credential_is_non_blocking():
    example = 'const apiKey = "sk-proj-xxxxx"\npassword = "password123"'
    report = scan_package(_instruction_package(skill_md=example))
    assert any(f["code"] == "plaintext_credential" for f in report["findings"])
    assert report["passed"] is True  # 示例凭据记录但不阻断上架


def test_scan_rule_real_provider_key_blocks():
    real = 'key = "sk-' + "A" * 32 + '"'
    report = scan_package(_plugin_package(scripts={"a.py": real}))
    assert report["passed"] is False


def test_scan_rule_fy_and_secret_env_names_flagged():
    report = scan_package(_instruction_package(skill_md="Read FY_SESSION_SECRET from env."))
    assert any(f["code"] == "plaintext_credential" for f in report["findings"])


def test_scan_report_finding_shape():
    report = scan_package(_plugin_package(scripts={"a.py": "import socket\nrm -rf /tmp/x\n"}))
    assert report["findings"], "should have findings"
    for f in report["findings"]:
        assert set(f.keys()) == {"code", "severity", "location", "message"}
        assert f["severity"] in {"critical", "high", "medium", "low"}
        assert f["location"]


def test_scan_report_structure_fields():
    report = scan_package(_plugin_package(scripts={"a.py": "import socket\n"}))
    assert report["scanner_version"]
    assert report["passed"] is False
    assert report["blocking_count"] >= 1
    assert list(report["blocking_severities"]) == list(BLOCKING_SEVERITIES)
    assert report["risk_level"] in {"critical", "high", "medium", "low", "none"}


def test_scan_metadata_keys_are_not_scanned():
    # name/license 等惰性元数据里的危险字面量不该产生发现。
    report = scan_package(_instruction_package(name="rm -rf everything"))
    assert report["finding_count"] == 0


def test_scan_findings_are_deduplicated():
    report = scan_package(_plugin_package(scripts={"a.py": "import socket\nimport socket\n"}))
    socket_findings = [f for f in report["findings"] if "socket" in f["message"]]
    assert len(socket_findings) == 1


def test_scan_detects_nested_extra_files():
    pkg = _plugin_package()
    pkg["files"] = {"deep/nested/x.py": "import ctypes\n"}
    report = scan_package(pkg)
    assert any(f["location"].startswith("files:") for f in report["findings"])


# ===========================================================================
# 3) 门禁策略
# ===========================================================================
def test_classify_package_instruction_vs_plugin():
    assert classify_package(_instruction_package()) == "instruction"
    assert classify_package(_plugin_package()) == "plugin"


def test_classify_file_entries_are_plugin():
    # 带会落盘的文件条目（映射/列表）→ plugin（要求签名）。
    assert classify_package({"name": "x", "skill_md": "# y", "scripts": {"main.py": "print(1)"}}) == "plugin"
    assert classify_package({"name": "x", "files": ["a.py", "b.py"]}) == "plugin"


def test_classify_inline_code_is_instruction_but_scanned():
    # 内联文本字段（如 code）没有落盘/执行路径 → instruction（不强制签名）；
    # 但其内容仍被强制扫描 —— 危险内容照样阻断（纵深防御，不构成绕过）。
    pkg = {"name": "summarizer", "code": "def run(x): return x[:10]"}
    assert classify_package(pkg) == "instruction"
    assert scan_package(pkg)["passed"] is True
    dangerous = {"name": "summarizer", "code": "import socket\nrm -rf /"}
    report = scan_package(dangerous)
    assert classify_package(dangerous) == "instruction"
    assert report["passed"] is False  # 内联代码里的危险内容仍被挡下


def test_gate_policy_is_server_side_constant():
    assert PROMOTION_GATE_POLICY["plugin"]["require_signature"] is True
    assert PROMOTION_GATE_POLICY["plugin"]["require_scan"] is True
    assert PROMOTION_GATE_POLICY["instruction"]["require_scan"] is True
    assert PROMOTION_GATE_POLICY["instruction"]["require_signature"] is False


def test_evaluate_promotion_gate_plugin_requires_both():
    blocked = evaluate_promotion_gate(profile="plugin", scan_passed=True, signature_verified=False)
    assert blocked["allowed"] is False
    assert "signature_not_verified" in blocked["reasons"]
    blocked2 = evaluate_promotion_gate(profile="plugin", scan_passed=False, signature_verified=True)
    assert blocked2["allowed"] is False
    assert "scan_not_passed" in blocked2["reasons"]
    ok = evaluate_promotion_gate(profile="plugin", scan_passed=True, signature_verified=True)
    assert ok["allowed"] is True


def test_evaluate_promotion_gate_unknown_profile_fails_closed():
    verdict = evaluate_promotion_gate(profile="mystery", scan_passed=True, signature_verified=True)
    assert verdict["allowed"] is False


def test_instruction_needs_scan_only():
    assert evaluate_promotion_gate(profile="instruction", scan_passed=True, signature_verified=False)["allowed"] is True
    assert evaluate_promotion_gate(profile="instruction", scan_passed=False, signature_verified=False)["allowed"] is False


# ===========================================================================
# 4) 服务集成：stage 记录扫描/签名
# ===========================================================================
def test_stage_records_scan_and_profile(session, owner):
    svc = SkillService(session, AuditService(session))
    sk = svc.stage(owner, name="clean", semantic_version="1.0.0",
                   package=_instruction_package(), source="internal", license_="MIT", domain="personal")
    assert sk.gate_profile == "instruction"
    assert sk.scan_passed is True
    assert sk.scan_report["passed"] is True
    assert sk.signature_verified is False


def test_stage_records_scan_failure(session, owner):
    svc = SkillService(session, AuditService(session))
    sk = svc.stage(owner, name="danger", semantic_version="1.0.0",
                   package=_plugin_package(scripts={"a.sh": "rm -rf /\n"}),
                   source="internal", license_="MIT", domain="personal")
    assert sk.scan_passed is False
    assert sk.gate_profile == "plugin"


def test_stage_verifies_valid_signature(session, owner):
    keys = SigningKeyService(session)
    priv, pub = generate_signing_keypair()
    keys.register(owner, key_id="key-1", public_key=pub)
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    sk = svc.stage(owner, name="signed", semantic_version="1.0.0", package=pkg,
                   source="internal", license_="MIT", domain="personal",
                   signature=sig, signing_key_id="key-1", signature_algorithm="ed25519")
    assert sk.signature_verified is True
    assert sk.signing_key_id == "key-1"
    assert sk.signature == sig


def test_stage_rejects_invalid_signature(session, owner):
    keys = SigningKeyService(session)
    _priv, pub = generate_signing_keypair()
    keys.register(owner, key_id="key-1", public_key=pub)
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    bad_sig = sign_package(private_key=generate_signing_keypair()[0], package=pkg)
    with pytest.raises(ValidationFailed) as exc:
        svc.stage(owner, name="badsig", semantic_version="1.0.0", package=pkg,
                  source="internal", license_="MIT", domain="personal",
                  signature=bad_sig, signing_key_id="key-1")
    assert exc.value.code == "signature_invalid"


def test_stage_rejects_unknown_signing_key(session, owner):
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    sig = sign_package(private_key=generate_signing_keypair()[0], package=pkg)
    with pytest.raises(ValidationFailed) as exc:
        svc.stage(owner, name="nokey", semantic_version="1.0.0", package=pkg,
                  source="internal", license_="MIT", domain="personal",
                  signature=sig, signing_key_id="does-not-exist")
    assert exc.value.code == "unknown_signing_key"


def test_stage_requires_key_id_when_signature_present(session, owner):
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    sig = sign_package(private_key=generate_signing_keypair()[0], package=pkg)
    with pytest.raises(ValidationFailed) as exc:
        svc.stage(owner, name="nokeyid", semantic_version="1.0.0", package=pkg,
                  source="internal", license_="MIT", domain="personal", signature=sig)
    assert exc.value.code == "signing_key_required"


def test_revoked_key_cannot_sign(session, owner):
    keys = SigningKeyService(session)
    priv, pub = generate_signing_keypair()
    keys.register(owner, key_id="key-r", public_key=pub)
    keys.revoke(owner, "key-r")
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    with pytest.raises(ValidationFailed) as exc:
        svc.stage(owner, name="revoked", semantic_version="1.0.0", package=pkg,
                  source="internal", license_="MIT", domain="personal",
                  signature=sig, signing_key_id="key-r")
    assert exc.value.code == "unknown_signing_key"


def test_register_rejects_bad_public_key(session, owner):
    keys = SigningKeyService(session)
    with pytest.raises(ValidationFailed):
        keys.register(owner, key_id="bad", public_key=base64.b64encode(b"short").decode())


def test_register_rejects_unknown_algorithm(session, owner):
    keys = SigningKeyService(session)
    _priv, pub = generate_signing_keypair()
    with pytest.raises(ValidationFailed):
        keys.register(owner, key_id="alg", public_key=pub, algorithm="rsa")


# ===========================================================================
# 5) 上架门禁（promote）
# ===========================================================================
def test_unsigned_plugin_cannot_promote(session, owner):
    svc = SkillService(session, AuditService(session))
    sk, ev = _stage_and_pass(svc, owner, _plugin_package(), "plug-unsigned")
    assert sk.gate_profile == "plugin"
    with pytest.raises(Conflict) as exc:
        svc.promote(owner, sk.id, ev.id)
    assert exc.value.code == "plugin_gate_blocked"
    assert "signature_not_verified" in str(exc.value)
    assert session.get(Skill, sk.id).state == "staged"


def test_scan_failed_plugin_cannot_promote_even_if_signed(session, owner):
    keys = SigningKeyService(session)
    priv, pub = generate_signing_keypair()
    keys.register(owner, key_id="k", public_key=pub)
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package(scripts={"a.sh": "rm -rf /\n"})
    sig = sign_package(private_key=priv, package=pkg)
    sk = svc.stage(owner, name="plug-badscan", semantic_version="1.0.0", package=pkg,
                   source="internal", license_="MIT", domain="personal",
                   signature=sig, signing_key_id="k")
    assert sk.scan_passed is False
    ev = svc.evaluate(owner, sk.id, static_passed=True, functional_passed=True)
    with pytest.raises(Conflict) as exc:
        svc.promote(owner, sk.id, ev.id)
    assert exc.value.code == "plugin_gate_blocked"
    assert "scan_not_passed" in str(exc.value)


def test_signed_and_scanned_plugin_promotes(session, owner):
    keys = SigningKeyService(session)
    priv, pub = generate_signing_keypair()
    keys.register(owner, key_id="k", public_key=pub)
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    sk = svc.stage(owner, name="plug-ok", semantic_version="1.0.0", package=pkg,
                   source="internal", license_="MIT", domain="personal",
                   signature=sig, signing_key_id="k")
    ev = svc.evaluate(owner, sk.id, static_passed=True, functional_passed=True)
    promoted = svc.promote(owner, sk.id, ev.id)
    assert promoted.state == "active"
    assert svc.can_invoke(sk.id) is True


def test_instruction_package_promotes_without_signature(session, owner):
    svc = SkillService(session, AuditService(session))
    sk, ev = _stage_and_pass(svc, owner, _instruction_package(), "instr-ok")
    assert sk.gate_profile == "instruction"
    promoted = svc.promote(owner, sk.id, ev.id)
    assert promoted.state == "active"


def test_scan_failed_instruction_cannot_promote(session, owner):
    svc = SkillService(session, AuditService(session))
    pkg = _instruction_package(skill_md="---\nname: bad\n---\nrm -rf /\n")
    sk, ev = _stage_and_pass(svc, owner, pkg, "instr-bad")
    assert sk.scan_passed is False
    with pytest.raises(Conflict) as exc:
        svc.promote(owner, sk.id, ev.id)
    assert exc.value.code == "plugin_gate_blocked"


def test_promote_rejects_gate_disabling_parameter(session, owner):
    svc = SkillService(session, AuditService(session))
    sk, ev = _stage_and_pass(svc, owner, _plugin_package(), "plug-nodisable")
    # 门禁是服务端策略；不存在任何「跳过」参数。
    with pytest.raises(TypeError):
        svc.promote(owner, sk.id, ev.id, skip_gate=True)  # type: ignore[call-arg]


def test_stage_rejects_forced_scan_parameter(session, owner):
    svc = SkillService(session, AuditService(session))
    # 调用方无法传参伪造扫描结论。
    with pytest.raises(TypeError):
        svc.stage(owner, name="x", semantic_version="1.0.0",
                  package=_plugin_package(scripts={"a.sh": "rm -rf /\n"}),
                  source="internal", license_="MIT", domain="personal",
                  scan_passed=True)  # type: ignore[call-arg]


def test_promotion_gate_status_reports_reasons(session, owner):
    svc = SkillService(session, AuditService(session))
    sk, _ev = _stage_and_pass(svc, owner, _plugin_package(), "plug-status")
    status = svc.promotion_gate_status(sk.id)
    assert status["allowed"] is False
    assert status["profile"] == "plugin"
    assert "signature_not_verified" in status["reasons"]


def test_gate_blocks_before_flipping_state(session, owner):
    svc = SkillService(session, AuditService(session))
    sk, ev = _stage_and_pass(svc, owner, _plugin_package(), "plug-nostate")
    with pytest.raises(Conflict):
        svc.promote(owner, sk.id, ev.id)
    # 失败后仍停在 staged，can_invoke 仍为 False。
    assert session.get(Skill, sk.id).state == "staged"
    assert svc.can_invoke(sk.id) is False


# ===========================================================================
# 6) 私钥不入库
# ===========================================================================
def test_plugin_signing_keys_table_has_no_private_key_column(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("plugin_signing_keys")}
    assert cols, "plugin_signing_keys table must exist"
    assert not any("private" in name for name in cols)
    assert "public_key" in cols


def test_private_key_never_persisted(session, owner, engine):
    keys = SigningKeyService(session)
    priv, pub = generate_signing_keypair()
    keys.register(owner, key_id="k", public_key=pub)
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    sk = svc.stage(owner, name="priv-check", semantic_version="1.0.0", package=pkg,
                   source="internal", license_="MIT", domain="personal",
                   signature=sig, signing_key_id="k")
    session.flush()

    # 逐列把整库扫一遍：私钥 b64 不得出现在任何表任何列里。
    metadata = inspect(engine)
    for table in metadata.get_table_names():
        for col in metadata.get_columns(table):
            type_name = col["type"].__class__.__name__.upper()
            if "CHAR" not in type_name and "TEXT" not in type_name and "JSON" not in type_name:
                continue
            rows = session.execute(text(f'SELECT "{col["name"]}" FROM {table}')).fetchall()
            for (value,) in rows:
                if value is None:
                    continue
                assert priv not in str(value), f"private key leaked into {table}.{col['name']}"
    # 签名值等于签名，绝不是私钥。
    assert sk.signature == sig
    assert sk.signature != priv


def test_audit_does_not_store_private_key_or_signature(session, owner):
    keys = SigningKeyService(session)
    priv, pub = generate_signing_keypair()
    keys.register(owner, key_id="k", public_key=pub, )
    svc = SkillService(session, AuditService(session))
    pkg = _plugin_package()
    sig = sign_package(private_key=priv, package=pkg)
    svc.stage(owner, name="audit-check", semantic_version="1.0.0", package=pkg,
              source="internal", license_="MIT", domain="personal",
              signature=sig, signing_key_id="k")
    session.flush()
    events = session.execute(text("SELECT details FROM audit_events")).fetchall()
    blob = " ".join(str(r[0]) for r in events)
    assert priv not in blob
    assert sig not in blob  # 连签名原文也不落审计
    assert "key" in blob or "k" in blob  # 但落 key_id


# ===========================================================================
# 7) 迁移可升可降
# ===========================================================================
def test_migration_0031_roundtrip(tmp_path):
    from alembic import command
    from alembic.config import Config

    if not ALEMBIC_INI.is_file():  # pragma: no cover
        pytest.skip("alembic.ini missing")
    db_path = tmp_path / "plug.db"
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    def _tables_and_cols():
        eng = create_engine(f"sqlite:///{db_path}")
        try:
            with eng.connect() as conn:
                tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
                cols = {r[1] for r in conn.execute(text("PRAGMA table_info(skills)"))}
            return tables, cols
        finally:
            eng.dispose()

    command.upgrade(cfg, "head")
    tables, cols = _tables_and_cols()
    assert "plugin_signing_keys" in tables
    assert {"signature", "signature_algorithm", "signing_key_id",
            "signature_verified", "scan_report", "scan_passed", "gate_profile"} <= cols

    command.downgrade(cfg, "0030_collaboration")
    tables, cols = _tables_and_cols()
    assert "plugin_signing_keys" not in tables
    assert "signature" not in cols and "gate_profile" not in cols

    command.upgrade(cfg, "head")
    tables, cols = _tables_and_cols()
    assert "plugin_signing_keys" in tables and "signature" in cols

    gc.collect()
    for _ in range(3):
        if not db_path.exists():
            break
        try:
            db_path.unlink()
        except OSError:  # pragma: no cover
            gc.collect()
            time.sleep(0.2)
