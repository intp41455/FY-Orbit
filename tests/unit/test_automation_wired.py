"""W10-B · 启动挂接断言（主控要求）。

验证 ``wire_automation_tools()`` 之后，受治理的 harness 网关单例的工具清单里
确实出现 automation.* 六个工具——否则 lifespan 挂接失效、生产上工具不存在。
"""

from __future__ import annotations

from find_yourself.services.automation import wire_automation_tools
from find_yourself.skills.harness import gateway


EXPECTED = {
    "automation.screenshot",
    "automation.list_windows",
    "automation.move_mouse",
    "automation.click",
    "automation.type_text",
    "automation.press_key",
}


def test_wire_registers_all_six_onto_governed_gateway() -> None:
    names = set(wire_automation_tools())
    assert EXPECTED.issubset(names)


def test_gateway_listing_contains_automation_tools() -> None:
    wire_automation_tools()
    listed = {t["name"] for t in gateway.list_tools()}
    assert EXPECTED.issubset(listed)


def test_automation_tools_carry_expected_levels() -> None:
    wire_automation_tools()
    by_name = {t["name"]: t for t in gateway.list_tools()}
    assert by_name["automation.screenshot"]["required_level"] == 3
    assert by_name["automation.click"]["required_level"] == 5
    assert by_name["automation.type_text"]["required_level"] == 5
