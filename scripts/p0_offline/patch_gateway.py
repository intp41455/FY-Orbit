"""一次性 codemod：把「默认离线」门接进 ModelGateway.complete（A-离线优先-01/03）。

为什么用脚本而不是手改：本仓库源码是 CRLF，多处锚点跨行，手改容易留下
行尾不一致。脚本按字节替换、先断言命中次数、再一次性落盘（先写 .tmp 再改名，
绝不出现半截文件）。幂等：已打过就报 already-applied。

门的管辖范围（刻意收窄，见 offline_block_reason 的 docstring）：
只拦**由 settings 配出来的远程路由**——那才是应用自己会出外网的路径。
本地推理（ollama，本机 socket）与宿主显式注入的适配器不在门内。
"""

from __future__ import annotations

import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "src/find_yourself/runtime/gateway.py"


def rep(buf: bytes, old: str, new: str, *, expect: int = 1) -> bytes:
    o, n = old.encode("utf-8"), new.encode("utf-8")
    found = buf.count(o)
    if found != expect:
        raise SystemExit(f"锚点命中 {found} 次（期望 {expect}）：{old[:70]!r}")
    return buf.replace(o, n)


def main() -> int:
    buf = PATH.read_bytes()
    if b"offline_block_reason" in buf:
        print("already-applied")
        return 0

    buf = rep(
        buf,
        "from ..services.errors import Conflict, PermissionDenied, ValidationFailed\r\n",
        "from ..services.errors import Conflict, PermissionDenied, ValidationFailed\r\n"
        "from ..services.offline import OfflineUnavailable, remote_block_reason\r\n",
    )

    buf = rep(buf, '    "MockModelProvider",\r\n', '    "MockModelProvider",\r\n    "OfflineUnavailable",\r\n')

    # 1) 记录「配置配出来的路由」快照（门只认这些对象本身）。
    buf = rep(
        buf,
        """        else:\r
            self.provider = None\r
            if self.settings is not None:\r
                self._wire_from_settings(fallbacks)\r
""",
        """        else:\r
            self.provider = None\r
            if self.settings is not None:\r
                self._wire_from_settings(fallbacks)\r
\r
        #: 由 settings 配出来的路由快照（**对象身份**，不是 provider_id）。\r
        #: 离线门只拦这些——它们才代表「应用自己会去连外网」的路径；\r
        #: 宿主注入的适配器与测试替换的替身不在此列（见 offline_block_reason）。\r
        self._settings_wired: list[ProviderRoute] = (\r
            [] if provider is not None else list(self.routes)\r
        )\r
""",
    )

    # 2) 门本体：插在 public API 分节标题之前（标题横线数量按文件现状取）。
    marker = b"    # -- public API"
    if buf.count(marker) != 1:
        raise SystemExit("public API 标题锚点不唯一")
    at = buf.index(marker)
    end = buf.index(b"\r\n", at) + 2
    heading = buf[at:end]
    gate = '''    # -- offline gate ------------------------------------------------------ #\r
    def offline_block_reason(self, route: ProviderRoute) -> str:\r
        """该路由是否被离线门拦住；允许时返回空串。\r
\r
        门只拦**由 settings 配出来的远程路由**（:attr:`_settings_wired`）——\r
        那才是应用自己会去连外网的路径。两类路由刻意不在门内，因为拦它们\r
        是拦错东西：\r
\r
        * 宿主 ``provider=`` 显式注入的适配器（自带推理 / 本地桩 / 测试替身）\r
          —— 显式接线不是「悄悄出网」，出不出网由宿主自己负责；\r
        * 本地推理 provider（如 ollama，连的是本机 socket）—— 「默认离线」\r
          拦的是**外网**，不是把本机模型也一起关掉；本机连不上时它自己会失败。\r
        """\r
        if not any(route is wired for wired in self._settings_wired):\r
            return ""\r
        if route.provider_id in LOCAL_INFERENCE_PROVIDERS:\r
            return ""\r
        return remote_block_reason(f"远程 provider「{route.provider_id}」")\r
\r
'''.encode("utf-8")
    buf = buf[:at] + gate + heading + buf[end:]

    # 3) 调用点：路由循环开头拦一道，拦下要带原因（降级链照常走）。
    buf = rep(
        buf,
        """        for index, route in enumerate(self.routes):\r
            target_model = route.model_for(model)\r
""",
        """        offline_blocked_routes: list[str] = []\r
\r
        for index, route in enumerate(self.routes):\r
            target_model = route.model_for(model)\r
\r
            # 0. 离线门（A-离线优先-01/03）：默认离线时远程路由**不出网**。\r
            #    拦下要带原因，让降级链照常往下走（本地路由顶上也如实标\r
            #    degraded_from/degraded_reason），而不是静默失败。\r
            offline_reason = self.offline_block_reason(route)\r
            if offline_reason:\r
                offline_blocked_routes.append(route.provider_id)\r
                reasons.append(f"{route.provider_id}:{target_model} → {offline_reason}")\r
                continue\r
""",
    )

    buf = rep(
        buf,
        "        raise ModelProviderUnavailable(requested_label, reasons) from last_exc\r\n",
        """        if offline_blocked_routes and len(offline_blocked_routes) == len(self.routes):\r
            # 每条路由都被离线门拦住：这不是「供应商挂了」，是**设计意图**。\r
            # 用专门的 code 让上层显示「离线模式已禁用远程调用」而非「网络错误」。\r
            raise OfflineUnavailable(f"远程模型调用（{requested_label}）") from last_exc\r
        raise ModelProviderUnavailable(requested_label, reasons) from last_exc\r
""",
    )

    tmp = PATH.with_suffix(".py.tmp")
    tmp.write_bytes(buf)
    tmp.replace(PATH)
    print(f"patched {PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
