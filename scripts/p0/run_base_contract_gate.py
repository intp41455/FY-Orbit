"""CI 门禁：运行基座接入校验（A-基座质保-11 / W1），按结果决定退出码。

`BaseContractService.audit_tree` 是一个**能真的 `exit_code=1`** 的硬门禁，但此前
仓库里没有任何 CI workflow 调用它 —— 门禁只在人手动跑时存在，CI 从不会因此拦下
合并（见 deliverables/engineering-assurance/gono-go-pre-launch-2026-10-09.md 的
P0-B1）。本脚本把它接成一条**非交互**命令：打印 scanned / violations 摘要，并在
存在阻塞性违规时以 `sys.exit(1)` 让 CI 如实报红。

与 `grant_base_exemptions.py` 的分工：豁免基线由后者按真实服务接口登记，本脚本
只负责**跑门禁并翻译退出码**，不掺任何「默认放过」的写法。

用法（PYTHONPATH 必须指向本树 src）：
    PYTHONPATH=<tree>/src python scripts/p0/run_base_contract_gate.py [--scan-root DIR]

``--scan-root`` 用于把门禁指向别的树（反向验证测试据此构造「被破坏的契约」
并断言本脚本真的会红；不给就是本仓）。环境变量 ``FY_BASE_SCAN_ROOT`` 次之，
两者都不给才回落到脚本所在仓库根。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from find_yourself.services.actor import Actor  # noqa: E402
from find_yourself.services.quality.base_contract import (  # noqa: E402
    DEFAULT_SCAN_DIRS,
    BaseContractService,
)


def _resolve_scan_root(cli_value: str | None) -> Path:
    """扫描根优先级：``--scan-root`` > ``FY_BASE_SCAN_ROOT`` > 本仓根。"""
    if cli_value:
        return Path(cli_value).resolve()
    env = os.environ.get("FY_BASE_SCAN_ROOT")
    return Path(env).resolve() if env else ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="基座接入硬门禁（fail-closed）")
    parser.add_argument("--scan-root", default=None,
                        help="扫描根目录；默认取 FY_BASE_SCAN_ROOT，再默认本仓根")
    args = parser.parse_args(argv)

    root = _resolve_scan_root(args.scan_root)
    # 豁免清单跟着扫描根走：门禁指向别处时不得读本仓的 .runtime（反之亦然），
    # 否则反向验证会被本仓既有豁免污染，测出来的红/绿都不可信。
    if not os.environ.get("FY_BASE_EXEMPTIONS"):
        os.environ["FY_BASE_EXEMPTIONS"] = str(root / ".runtime" / "quality"
                                               / "base_exemptions.json")

    missing = [d for d in DEFAULT_SCAN_DIRS if not (root / d).is_dir()]
    if missing:
        # **假绿的头号来源**：扫描目录被改名 / 未 checkout 出来时，扫描结果为 0 个
        # 文件、0 条违规，旧写法会「通过」——门禁恰恰在最需要它的时候消失。
        for d in missing:
            print(f"::error:: 扫描目录不存在：{root / d}（DEFAULT_SCAN_DIRS={list(DEFAULT_SCAN_DIRS)}）")
        print("阻塞（fail-closed）：扫描面缺失，无法判定基座接入状态。")
        return 1

    eng = create_engine("sqlite://", future=True)
    session = sessionmaker(bind=eng, future=True)()
    svc = BaseContractService(session, root=root)
    result = svc.audit_tree(Actor.owner("ci-base-contract-gate"),
                            root=root, dirs=list(DEFAULT_SCAN_DIRS))

    print(f"基座接入校验：root={root} scanned={result['scanned']} "
          f"violations={result['violation_count']} "
          f"advisories={result['advisory_count']}")
    for v in result["violations"]:
        print(f"::error:: {v['path']} [{v['kind']}] {v['message']}")

    if not result["scanned"]:
        # scanned=0 且无违规 = 一个文件都没看到。这不是「干净」，是「没扫到」。
        print("::error:: 扫描到 0 个文件——门禁不得在无扫描面时报告通过。")
        print("阻塞（fail-closed）：扫描结果为空，无法证明契约已满足。")
        return 1

    if result["violations"]:
        print(f"阻塞（fail-closed）：{result['ci']['reason']}")
        return 1
    print("通过：全部页面与可编辑组件已接入基座或已获审批豁免。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
