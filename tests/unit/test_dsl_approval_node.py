"""ADR-04 ·DSL ``approval`` 治理节点的契约单测。

本文件钉住三条硬约束：

1. **编译期安全边界**——``approval.op`` 必须属于
   ``services/proposal.py`` 的 ``IMMEDIATE_OPS ∪ EXTERNAL_OPS``，否则编译错误，
   且错误信息点名那个不被支持的 op。白名单**唯一真源是 proposal**，DSL 不另抄一份。
2. **运行期只挂起、绝不自己批**——DSL 引擎不实现任何审批逻辑（不重算 digest、
   不比对、不写状态、不消费 idempotency_key、不建 outbox），只把「创建提案意图」
   随 :class:`DslSuspended` 交给上层编排；``approval`` 与 ``confirm`` 共用同一挂起点。
3. **新增动词自动同步**——``DSL_JSON_SCHEMA`` 与 IR 派生的参数模型都从
   ``VERB_REGISTRY`` 派生，不可能漂移。

变异判据：把 ``VERB_REGISTRY['approval'].check`` 摘掉后，
``test_mutation_removing_whitelist_check_lets_illegal_op_compile`` 必须变红；
把 :func:`validate_ir` 改成恒返回 ``[]`` 时，IR 诊断用例必须变红。
"""

from __future__ import annotations

import dataclasses

import pytest

from find_yourself.services.dsl_canvas import (
    DSL_JSON_SCHEMA,
    TRANSFORM_VERBS,
    VERB_REGISTRY,
    DslSuspended,
    DslValidationError,
    allowed_approval_ops,
    compile_dsl,
    dsl_digest,
    run_dsl,
    validate_dsl,
    verb_catalog,
)
from find_yourself.services.dsl_ir import (
    TRANSFORM_PARAMS_MODELS,
    assert_ir_valid,
    params_model,
    validate_ir,
)
from find_yourself.services.proposal import EXTERNAL_OPS, IMMEDIATE_OPS

#: 从真实白名单各取一个代表，保证用例锚定的是 proposal.py 的真值。
_AN_IMMEDIATE_OP = "memory.upsert"
_AN_EXTERNAL_OP = "task.merge"


# --------------------------------------------------------------------------- #
# 构造器
# --------------------------------------------------------------------------- #
def _doc(nodes: list[dict], edges: list[dict]) -> dict:
    return {"version": "1", "nodes": nodes, "edges": edges}


def _lit(value=None) -> dict:
    return {"id": "src", "type": "input",
            "params": {"kind": "literal", "value": value}}


def _approval(node_id: str, **params) -> dict:
    return {"id": node_id, "type": "transform", "verb": "approval",
            "params": params}


def _out(node_id: str = "out") -> dict:
    return {"id": node_id, "type": "output", "params": {"format": "json"}}


def _approval_doc(**params) -> dict:
    return _doc([_lit({"amount": 100}), _approval("ap", **params), _out()],
                [{"from": "src", "to": "ap"}, {"from": "ap", "to": "out"}])


# =========================================================================== #
# 1. 注册与自动同步（动词可见、schema/IR 同源派生）
# =========================================================================== #
class TestApprovalIsRegisteredAndInSync:
    def test_verb_is_registered(self):
        assert "approval" in VERB_REGISTRY
        assert "approval" in TRANSFORM_VERBS

    def test_verb_is_visible_in_catalog(self):
        entry = next(v for v in verb_catalog() if v["name"] == "approval")
        assert entry["category"] and entry["summary"]
        assert entry["params_schema"]["type"] == "object"
        assert entry["params_schema"]["additionalProperties"] is False
        assert "op" in entry["params_schema"]["required"]

    def test_json_schema_enum_includes_approval(self):
        enum = DSL_JSON_SCHEMA["properties"]["nodes"]["items"]["properties"]["verb"]["enum"]
        assert enum == list(TRANSFORM_VERBS)
        assert "approval" in enum

    def test_ir_params_model_is_derived_for_approval(self):
        assert "approval" in TRANSFORM_PARAMS_MODELS
        assert params_model("approval") is TRANSFORM_PARAMS_MODELS["approval"]
        model = TRANSFORM_PARAMS_MODELS["approval"]
        schema = VERB_REGISTRY["approval"].params_schema
        assert set(model.model_fields) == set(schema["properties"])
        assert model.model_fields["op"].is_required()

    def test_transitive_sync_cannot_drift(self):
        """注册表 / 白名单 / IR 模型三者同源，不可能各自漂移。"""
        assert set(TRANSFORM_VERBS) == set(VERB_REGISTRY)
        assert set(TRANSFORM_PARAMS_MODELS) == set(TRANSFORM_VERBS)

    def test_op_schema_does_not_hardcode_the_whitelist(self):
        """白名单**不**硬编码进 schema —— 真源只能是 proposal.py。"""
        props = VERB_REGISTRY["approval"].params_schema["properties"]
        assert "enum" not in props["op"]
        assert props["op"] == {"type": "string", "minLength": 1}


# =========================================================================== #
# 2. 编译期：合法 op 通过（各取 IMMEDIATE 与 EXTERNAL 至少一个）
# =========================================================================== #
class TestLegalOpsCompile:
    def test_immediate_op_compiles(self):
        assert _AN_IMMEDIATE_OP in IMMEDIATE_OPS  # 锚定真值，防止用例过期
        assert compile_dsl(_approval_doc(op=_AN_IMMEDIATE_OP))

    def test_external_op_compiles(self):
        assert _AN_EXTERNAL_OP in EXTERNAL_OPS
        assert compile_dsl(_approval_doc(op=_AN_EXTERNAL_OP))

    def test_whitelist_is_exactly_the_proposal_union(self):
        """唯一真源校验：DSL 白名单 == proposal.IMMEDIATE_OPS ∪ EXTERNAL_OPS。"""
        assert allowed_approval_ops() == frozenset(IMMEDIATE_OPS) | frozenset(EXTERNAL_OPS)

    def test_every_whitelisted_op_compiles(self):
        for op in sorted(allowed_approval_ops()):
            assert compile_dsl(_approval_doc(op=op)), op


# =========================================================================== #
# 3. 编译期：非法 op 被拒，且错误点名那个 op（安全边界）
# =========================================================================== #
class TestIllegalOpsAreRejected:
    @pytest.mark.parametrize("bad_op", [
        "task.drop", "memory.upsert_evil", "Memory.Upsert", "memory.upsert ",
        "approval", "update",
    ])
    def test_illegal_op_is_rejected_and_named(self, bad_op: str):
        with pytest.raises(DslValidationError) as exc:
            validate_dsl(_approval_doc(op=bad_op))
        message = str(exc.value)
        assert bad_op in message            # 点名不被支持的 op
        assert "proposal" in message        # 并告知真源/允许集来源

    def test_empty_op_is_rejected(self):
        with pytest.raises(DslValidationError) as exc:
            validate_dsl(_approval_doc(op=""))
        assert "op" in str(exc.value)

    def test_non_string_op_is_rejected(self):
        with pytest.raises(DslValidationError) as exc:
            validate_dsl(_approval_doc(op=42))
        assert "op" in str(exc.value)

    def test_missing_op_is_rejected(self):
        doc = _doc([_lit({"a": 1}), _approval("ap", reason="只给理由"),
                    _out()],
                   [{"from": "src", "to": "ap"}, {"from": "ap", "to": "out"}])
        with pytest.raises(DslValidationError) as exc:
            validate_dsl(doc)
        assert "op" in str(exc.value)

    def test_compile_dsl_rejects_illegal_op_too(self):
        """不能只靠上层：编译器入口也必须拦住。"""
        with pytest.raises(DslValidationError):
            compile_dsl(_approval_doc(op="task.drop"))

    def test_document_cannot_smuggle_an_approval_flag(self):
        """文档里自称「已批准」塞不进参数契约（封闭）。"""
        with pytest.raises(DslValidationError, match="approved"):
            validate_dsl(_approval_doc(op=_AN_IMMEDIATE_OP, approved=True))


# =========================================================================== #
# 4. IR 诊断能定位到 approval 的 op 字段
# =========================================================================== #
class TestIrDiagnosticLocatesOpField:
    def test_illegal_op_yields_precise_diagnostic(self):
        diags = validate_ir(_approval_doc(op="task.drop"))
        hit = next((d for d in diags
                    if d.node_id == "ap" and d.field_path == "params.op"), None)
        assert hit is not None, [d.to_dict() for d in diags]
        assert hit.code == "cross_field"
        assert "task.drop" in hit.message   # 诊断里点名具体 op

    def test_illegal_op_blocks_compile_and_ir_gate(self):
        """非法 op 在执行前就被拦下：编译入口 + IR 闸门都不放行。"""
        with pytest.raises(DslValidationError) as exc:
            compile_dsl(_approval_doc(op="task.drop"))
        assert "task.drop" in str(exc.value)   # 编译入口点名该 op
        with pytest.raises(DslValidationError, match="DSL 类型校验"):
            assert_ir_valid(_approval_doc(op="task.drop"))  # IR 闸门同样拦截

    def test_valid_op_produces_no_diagnostic(self):
        assert validate_ir(_approval_doc(op=_AN_EXTERNAL_OP)) == []


# =========================================================================== #
# 5. 运行期：只挂起，绝不自己批（ADR-04 的核心）
# =========================================================================== #
class TestApprovalOnlySuspends:
    def test_running_approval_suspends(self):
        with pytest.raises(DslSuspended) as exc:
            run_dsl(_approval_doc(op=_AN_IMMEDIATE_OP), execution_id="exec-ap")
        susp = exc.value
        assert susp.node_id == "ap"
        assert susp.checkpoint == "exec-ap#ap"
        # 携带「创建提案意图」，字段名对齐 ProposalService.create。
        approval = susp.context["approval"]
        assert approval["operation"] == _AN_IMMEDIATE_OP
        assert approval["payload"] == {"amount": 100}
        assert [o["value"] for o in susp.options] == ["approve", "reject"]

    def test_suspension_carries_document_digest(self):
        doc = _approval_doc(op=_AN_IMMEDIATE_OP)
        with pytest.raises(DslSuspended) as exc:
            run_dsl(doc, execution_id="e")
        assert exc.value.dsl_digest == dsl_digest(doc)

    def test_suspension_is_not_downgraded_to_failure(self):
        """DslSuspended 必须穿透执行器的 except Exception（否则假绿）。"""
        with pytest.raises(DslSuspended):
            run_dsl(_approval_doc(op=_AN_EXTERNAL_OP), execution_id="e")

    def test_running_approval_never_touches_the_governance_write_path(self, monkeypatch):
        """证明「没有新写审批逻辑」：运行 approval 时 proposal 写路径一次都没被调用。"""
        from find_yourself.services import proposal as proposal_mod

        calls: list[str] = []
        monkeypatch.setattr(proposal_mod.ProposalService, "create",
                            lambda *a, **k: calls.append("create"))
        monkeypatch.setattr(proposal_mod.ProposalService, "decide",
                            lambda *a, **k: calls.append("decide"))
        with pytest.raises(DslSuspended):
            run_dsl(_approval_doc(op=_AN_IMMEDIATE_OP), execution_id="e")
        assert calls == []

    def test_approval_execute_source_contains_no_governance_logic(self):
        """静态证据：approval 执行体里没有 digest/并发 claim/状态写入等治理逻辑。

        剥掉文档字符串后扫描真正的代码体（注释/说明里出现这些词是刻意的「不做什么」）。
        """
        import ast
        import inspect
        import textwrap

        from find_yourself.services import dsl_canvas as dc

        fn = ast.parse(textwrap.dedent(inspect.getsource(dc._exec_approval))).body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)
                and isinstance(fn.body[0].value.value, str)):
            fn.body = fn.body[1:]  # 去掉 docstring
        src = ast.unparse(fn)
        for banned in ("compute_digest", "canonical_fields", "idempotency_key",
                       "rowcount", "update(", "Proposal(", "status=", "outbox"):
            assert banned not in src, banned
        # 运行期只抛出唯一的挂起信号，没有任何「自己批」的返回路径。
        assert "DslSuspended(" in src

    def test_dsl_canvas_does_not_import_governance_at_module_level(self):
        """惰性导入证据：dsl_canvas 模块级没有 proposal 的写路径绑定。"""
        from find_yourself.services import dsl_canvas as dc

        for name in ("ProposalService", "Proposal", "Operation", "IMMEDIATE_OPS",
                     "EXTERNAL_OPS", "update"):
            assert not hasattr(dc, name), name


# =========================================================================== #
# 6. 运行期：等待治理层 signal（approve / reject / 尚无裁决）
# =========================================================================== #
class TestApprovalWaitsForGovernanceSignal:
    def test_approve_signal_resumes_and_passes_payload(self):
        result = run_dsl(_approval_doc(op=_AN_IMMEDIATE_OP), execution_id="e",
                         approval_signal=lambda nid, ex: "approve")
        assert result.status == "succeeded", result.error
        assert result.output == {"amount": 100}

    def test_reject_signal_fails_explicitly(self):
        result = run_dsl(_approval_doc(op=_AN_IMMEDIATE_OP), execution_id="e",
                         approval_signal=lambda nid, ex: "reject")
        assert result.status == "failed"
        assert "驳回" in (result.error or "")
        assert next(log for log in result.logs
                    if log.node_id == "out").status == "skipped"

    def test_signal_reader_seeing_nothing_still_suspends(self):
        """读取器说「尚无裁决」→ 仍挂起，绝不猜成 approve。"""
        with pytest.raises(DslSuspended):
            run_dsl(_approval_doc(op=_AN_IMMEDIATE_OP), execution_id="e",
                    approval_signal=lambda nid, ex: None)


# =========================================================================== #
# 7. 变异判据：白名单校验是被测的安全边界本身
# =========================================================================== #
def test_mutation_removing_whitelist_check_lets_illegal_op_compile(monkeypatch):
    """摘掉 approval 的白名单 check 后，'task.drop' 必须能编译通过。

    这证明「非法 op 被拒」确实来自白名单校验（而非 schema 的其它约束）——
    即本文件钉住的是 ADR-04 的安全边界，而不是一条可有可无的断言。
    """
    from find_yourself.services import dsl_canvas as dc

    original = dc.VERB_REGISTRY["approval"]
    mutant = dataclasses.replace(original, check=None)
    monkeypatch.setitem(dc.VERB_REGISTRY, "approval", mutant)

    doc = _approval_doc(op="task.drop")
    dc.validate_dsl(doc)   # 变异体不抛 = 校验确实来自 check
    dc.compile_dsl(doc)    # IR 闸门也读同一 check，同样放行
