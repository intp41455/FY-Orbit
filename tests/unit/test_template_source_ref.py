"""G4-a 实体化引用：实例化输出携带源引用 + 可查新旧。

* ``instantiate`` 输出顶层 ``source_ref``（template_id/tier/schema_version/
  instantiated_at），旧 ``template_id`` 原样保留（向后兼容）；
* ``source_status(source_ref)`` 对照当前注册表：版本一致即 fresh，
  注册表已演进即 stale（联动入口）；未知模板/坏形状诚实报错，不编造。
"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.templates.scaffold import (
    TEMPLATE_SCHEMA_VERSION,
    ScaffoldTemplateService,
)


@pytest.fixture()
def service(tmp_path) -> ScaffoldTemplateService:
    return ScaffoldTemplateService(user_dir=tmp_path / "user-templates")


@pytest.fixture()
def owner():
    from find_yourself.services.actor import Actor

    return Actor.owner("owner-1")


def test_instantiate_stamps_source_ref(service, owner) -> None:
    out = service.instantiate(owner, "writing-pipeline")
    ref = out["source_ref"]
    assert ref["template_id"] == "writing-pipeline"
    assert ref["tier"] == "novice"
    assert ref["schema_version"] == TEMPLATE_SCHEMA_VERSION
    assert ref["instantiated_at"]
    # 旧字段原样保留（调用方零改动）。
    assert out["template_id"] == "writing-pipeline"


def test_source_status_fresh_when_registry_unchanged(service, owner) -> None:
    out = service.instantiate(owner, "writing-pipeline")
    st = service.source_status(out["source_ref"])
    assert st["stale"] is False
    assert st["current_schema_version"] == TEMPLATE_SCHEMA_VERSION


def test_source_status_stale_when_schema_moved_on(service, owner) -> None:
    out = service.instantiate(owner, "writing-pipeline")
    ref = dict(out["source_ref"], schema_version="0.0.0-ancient")
    st = service.source_status(ref)
    assert st["stale"] is True
    assert st["current_schema_version"] == TEMPLATE_SCHEMA_VERSION


def test_source_status_rejects_unknown_template_and_bad_shape(service) -> None:
    with pytest.raises(NotFound, match="模板不存在"):
        service.source_status({"template_id": "nope", "tier": "novice",
                               "schema_version": "1.0.0"})
    with pytest.raises(ValidationFailed, match="source_ref"):
        service.source_status({"tier": "novice"})
