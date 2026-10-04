"""系统视觉/输入封装 (W10-B).

把截图、鼠标、键盘、窗口枚举这些「真会动用户电脑」的调用，全部收敛在这一层：

* **懒加载**：``import mss`` / ``import pyautogui`` / ``import pygetwindow`` 只在
  对应方法**真正被调用**时才发生。模块 import 本身不要求装这些包——否则后端
  一启动就崩，违背「本地优先、双击即用」。
* **缺依赖就诚实报错**：包没装时抛 :class:`AutomationDepsMissing`（明确告诉
  用户要装什么），**绝不**用一张假截图或假坐标冒充真系统状态。
* **可注入 fake**：测试把 :class:`FakeAutomationBackend` 装进来，全程不碰真
  鼠标键盘（任务书 §W10-B.4 强制）。

视觉诚实边界：截图工具返回的是**原始像素帧**（PNG base64）。本层**不做 OCR、
不做视觉理解**——那是用户配置的 vision 多模态模型的事。返回值里带一个
``note`` 字段如实说明这一点，避免上游误以为已经识别出屏幕内容。
"""

from __future__ import annotations

import base64
from typing import Any, Protocol

from ..errors import DomainError


class AutomationDepsMissing(DomainError):
    """本机缺少 GUI 自动化依赖（mss / pyautogui / pygetwindow）。"""

    http_status = 503
    default_code = "automation_deps_missing"


#: 视觉帧里固定携带的诚实提示——本工具不做 OCR/语义理解。
VISION_HONESTY_NOTE = (
    "raw pixel frame returned; NO OCR or on-screen understanding was performed. "
    "Feed this image to a vision-capable model for semantic interpretation. "
    "If no vision model is configured, this image alone is opaque."
)


class AutomationBackend(Protocol):
    """系统视觉/输入能力的抽象边界。"""

    def screenshot(self) -> dict[str, Any]: ...
    def list_windows(self) -> list[dict[str, Any]]: ...
    def move_mouse(self, x: int, y: int) -> dict[str, Any]: ...
    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> dict[str, Any]: ...
    def type_text(self, text: str) -> dict[str, Any]: ...
    def press_key(self, key: str) -> dict[str, Any]: ...


class SystemAutomationBackend:
    """真实后端：调用 mss / pyautogui / pygetwindow。

    所有重依赖在方法内 import。构造不做任何系统动作。
    """

    def screenshot(self) -> dict[str, Any]:
        try:
            import mss  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise AutomationDepsMissing(
                "automation_deps_missing",
                "截图能力需要安装 'mss'。请用阿里云镜像安装后再启用："
                " uv add mss  (并在 settings 面板开启相应权限档位)。",
            ) from exc

        # mss 是上下文管理器；抓主屏（monitor 1）一帧。
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)
            # PNG 编码交给 mss 的 to_png 线路，避免再引 Pillow。
            png_bytes = mss.tools.to_png(raw.rgb, raw.size)
            width, height = raw.size
        return {
            "image_format": "png",
            "image_b64": base64.b64encode(png_bytes).decode("ascii"),
            "width": width,
            "height": height,
            "note": VISION_HONESTY_NOTE,
        }

    def list_windows(self) -> list[dict[str, Any]]:
        try:
            import pygetwindow as gw  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise AutomationDepsMissing(
                "automation_deps_missing",
                "窗口枚举能力需要安装 'pygetwindow' (及 pywin32)。",
            ) from exc
        out: list[dict[str, Any]] = []
        for w in gw.getAllWindows():
            title = getattr(w, "title", "") or ""
            if not title.strip():
                continue
            out.append(
                {
                    "title": title,
                    "left": getattr(w, "left", None),
                    "top": getattr(w, "top", None),
                    "width": getattr(w, "width", None),
                    "height": getattr(w, "height", None),
                }
            )
        return out

    def _pyautogui(self):
        try:
            import pyautogui  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise AutomationDepsMissing(
                "automation_deps_missing",
                "鼠标/键盘注入能力需要安装 'pyautogui'。",
            ) from exc
        return pyautogui

    def move_mouse(self, x: int, y: int) -> dict[str, Any]:
        pg = self._pyautogui()
        pg.moveTo(x, y)
        return {"x": x, "y": y, "action": "move"}

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        pg = self._pyautogui()
        pg.click(x=x, y=y, button=button, clicks=clicks)
        return {"x": x, "y": y, "button": button, "clicks": clicks, "action": "click"}

    def type_text(self, text: str) -> dict[str, Any]:
        pg = self._pyautogui()
        # interval 保持 0：不人为拖慢；这是受控注入，不是拟人脚本。
        pg.typewrite(text, interval=0.0)
        return {"chars": len(text), "action": "type"}

    def press_key(self, key: str) -> dict[str, Any]:
        pg = self._pyautogui()
        pg.press(key)
        return {"key": key, "action": "press"}


class FakeAutomationBackend:
    """测试用后端：记录每次调用，返回确定性结果，绝不碰真系统。

    可选注入 ``script_error`` 让某个方法抛错，用于验证错误路径。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def screenshot(self) -> dict[str, Any]:
        self.calls.append({"op": "screenshot"})
        return {
            "image_format": "png",
            "image_b64": base64.b64encode(b"FAKE_PIXELS").decode("ascii"),
            "width": 8,
            "height": 6,
            "note": VISION_HONESTY_NOTE,
        }

    def list_windows(self) -> list[dict[str, Any]]:
        self.calls.append({"op": "list_windows"})
        return [{"title": "Notepad", "left": 0, "top": 0, "width": 200, "height": 100}]

    def move_mouse(self, x: int, y: int) -> dict[str, Any]:
        self.calls.append({"op": "move", "x": x, "y": y})
        return {"x": x, "y": y, "action": "move"}

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self.calls.append({"op": "click", "x": x, "y": y, "button": button, "clicks": clicks})
        return {"x": x, "y": y, "button": button, "clicks": clicks, "action": "click"}

    def type_text(self, text: str) -> dict[str, Any]:
        self.calls.append({"op": "type", "text": text})
        return {"chars": len(text), "action": "type"}

    def press_key(self, key: str) -> dict[str, Any]:
        self.calls.append({"op": "press", "key": key})
        return {"key": key, "action": "press"}
