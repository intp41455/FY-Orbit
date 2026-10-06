"""任务市场（A-任务可移植-04 第三层）—— **本地目录式**挂卖与分发。

⚠️ **范围（这就是本模块的诚实边界）**：需求原文是「跑通挂市场卖 5 块，别人买下
导入输入产品直接跑」。**账号与支付不在本轮范围内**——派单通知书明确要求
「含账号/交易，属合规敏感面，实现前必须与主控确认是否本轮做、做到什么程度」。
所以本模块只做**挂卖与分发机制**本身，并把交易缺口显式暴露在返回值里
（``payments_supported: False``），而不是假装已经能收钱。

做到的事：

* **挂卖**：只接受 ``kind == "task_template"`` 的文档（任务实例不进市场）；
  发布前过 :func:`~find_yourself.services.clawtask.format.validate_clawtask`
  + 摘要封存，**不合格一律拒绝**（不写半个条目）。
* **检索**：本地目录 + 索引文件，支持关键词 / 场景过滤 + 有界分页。
* **导入**：读回文件 → 校验摘要（被改过就拒绝）→ 返回文档供调用方实例化。
* **完整性**：条目文件里的 ``integrity.digest`` 是唯一真源；索引里的 digest
  与文件不一致时**以文件为准并报错**，防止索引与文件漂移被当成两份内容。

存储：``FY_CLAWTASK_MARKET_DIR``（默认 ``.runtime/clawtask-market``）。
与 ``services/snapshot.py`` 落 ``.runtime/snapshots`` 同一范式，**零新表**。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..actor import Actor
from ..audit import AuditService
from ..errors import NotFound, PermissionDenied, ValidationFailed
from .format import (
    CLAWTASK_EXT,
    CLAWTASK_VERSION,
    clawtask_digest,
    parse,
    serialize,
    validate_clawtask,
)

_INDEX_NAME = "index.json"
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100


def market_dir() -> Path:
    return Path(os.environ.get("FY_CLAWTASK_MARKET_DIR", ".runtime/clawtask-market"))


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", (text or "").strip(), flags=re.UNICODE)
    return s.strip("-").lower() or "task-template"


class ClawTaskMarket:
    """本地任务市场。``owner_id`` 用于区分「谁上架的」（不构成权限边界）。"""

    def __init__(self, *, directory: str | os.PathLike[str] | None = None,
                 audit: AuditService | None = None) -> None:
        self._dir = Path(directory) if directory else market_dir()
        self.audit = audit

    # -- 索引 ---------------------------------------------------------------
    def _index_path(self) -> Path:
        return self._dir / _INDEX_NAME

    def _read_index(self) -> list[dict[str, Any]]:
        path = self._index_path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def _write_index(self, items: list[dict[str, Any]]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path().write_text(
            json.dumps(items, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )

    # -- 挂卖 ---------------------------------------------------------------
    def publish(self, actor: Actor, doc: dict[str, Any], *, version: str = "1.0.0") -> dict[str, Any]:
        """上架一个**任务模板**。任务实例（kind=task）不接受。"""
        actor.require_authenticated()
        if doc.get("kind") != "task_template":
            raise ValidationFailed(
                "market_only_task_templates",
                f"只有 kind=task_template 能上架，实际 {doc.get('kind')!r}"
                "（任务实例请用冬眠机制封存，不进市场）",
            )
        problems = validate_clawtask(doc, check_integrity=False)
        problems = [p for p in problems if not p.startswith("integrity_")]
        if problems:
            raise ValidationFailed("market_entry_invalid",
                                  "条目不合格，拒绝上架：" + "；".join(problems))

        item_id = f"{_slug(str(doc.get('name') or doc.get('id')))}@{version}"
        filename = f"{item_id}{CLAWTASK_EXT}"
        self._dir.mkdir(parents=True, exist_ok=True)
        exported = serialize(doc, portable=True)   # 市场条目一律可移植（不带本机痕迹）
        (self._dir / filename).write_text(exported["text"], encoding="utf-8", newline="\n")

        entry = {
            "item_id": item_id,
            "name": doc.get("name"),
            "version": version,
            "filename": filename,
            "digest": exported["digest"],
            "clawtask_version": CLAWTASK_VERSION,
            "goal": doc.get("goal"),
            "publisher": getattr(actor, "owner_id", "") or getattr(actor, "service_id", ""),
            "target_models": list(doc.get("target_models") or []),
            "template_id": (doc.get("system_template") or {}).get("template_id"),
            "step_count": len(doc.get("steps") or []),
            # 交易缺口显式暴露：本轮无账号 / 无支付
            "pricing": {"payments_supported": False, "price": None,
                        "note": "账号与支付不在本轮范围，待主控确认后单独立项"},
        }
        items = [it for it in self._read_index() if it.get("item_id") != item_id]
        items.append(entry)
        items.sort(key=lambda it: str(it.get("item_id")))
        self._write_index(items)
        if self.audit is not None:
            self.audit.append(actor, "clawtask.market_published", item_id,
                              {"digest": exported["digest"], "bytes": exported["byte_size"]})
        return entry

    # -- 检索 ---------------------------------------------------------------
    def list_items(self, actor: Actor, *, query: str = "", template_id: str | None = None,
                   limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0) -> dict[str, Any]:
        actor.require_authenticated()
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValidationFailed("page_limit_invalid", "limit 必须是 >= 1 的整数")
        if limit > MAX_PAGE_LIMIT:
            raise ValidationFailed("page_limit_exceeded",
                                   f"limit 超过上界 {MAX_PAGE_LIMIT}")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValidationFailed("page_offset_invalid", "offset 必须是 >= 0 的整数")

        needle = (query or "").strip().lower()
        rows = []
        for entry in self._read_index():
            if needle and needle not in str(entry.get("name", "")).lower() \
                    and needle not in str(entry.get("goal", "")).lower():
                continue
            if template_id is not None and entry.get("template_id") != template_id:
                continue
            rows.append(entry)
        return {"items": rows[offset:offset + limit], "total": len(rows),
                "limit": limit, "offset": offset, "payments_supported": False}

    def get_item(self, actor: Actor, item_id: str) -> dict[str, Any]:
        actor.require_authenticated()
        entry = next((e for e in self._read_index() if e.get("item_id") == item_id), None)
        if entry is None:
            raise NotFound("market_item_not_found", f"市场条目不存在：{item_id}")
        return entry

    # -- 导入 ---------------------------------------------------------------
    def import_item(self, actor: Actor, item_id: str) -> dict[str, Any]:
        """导入一个市场条目 → ``(entry, doc)`` 形状的响应。

        * 文件缺失 → 404（索引说有、文件没有，如实报错，不编造条目）。
        * 摘要不符 → 拒绝（``market_entry_tampered``）。
        * 交付物的 ``payments_supported`` 恒为 ``False``：没有支付，就没有「购买」。
        """
        actor.require_authenticated()
        entry = self.get_item(actor, item_id)
        path = self._dir / str(entry["filename"])
        if not path.is_file():
            raise NotFound("market_entry_file_missing",
                           f"索引里有 {item_id}，但文件 {entry['filename']} 不存在")
        text = path.read_text(encoding="utf-8")
        doc = parse(text, verify=True)                      # 摘要不符会抛
        file_digest = clawtask_digest(doc)
        if entry.get("digest") and entry["digest"] != file_digest:
            # 索引与文件漂移：以文件为准但**明确报错**，不悄悄接受
            raise ValidationFailed(
                "market_entry_tampered",
                f"索引摘要与文件不一致（index={entry['digest'][:12]}…, "
                f"file={file_digest[:12]}…）；拒绝导入",
            )
        if self.audit is not None:
            self.audit.append(actor, "clawtask.market_imported", item_id,
                              {"digest": file_digest})
        return {
            "item_id": item_id,
            "entry": entry,
            "doc": doc,
            "payments_supported": False,
            "note": "已导入任务模板；输入产品即可跑。本轮不含账号与支付。",
        }

    def unpublish(self, actor: Actor, item_id: str) -> dict[str, Any]:
        """下架。只有上架者本人可下架（本地目录里的权限边界）。"""
        actor.require_authenticated()
        entry = self.get_item(actor, item_id)
        me = getattr(actor, "owner_id", "") or getattr(actor, "service_id", "")
        if entry.get("publisher") and entry["publisher"] != me:
            raise PermissionDenied("market_not_publisher", "只有上架者本人可以下架")
        items = [it for it in self._read_index() if it.get("item_id") != item_id]
        self._write_index(items)
        (self._dir / str(entry["filename"])).unlink(missing_ok=True)
        if self.audit is not None:
            self.audit.append(actor, "clawtask.market_unpublished", item_id, {})
        return {"item_id": item_id, "removed": True}


__all__ = ["ClawTaskMarket", "market_dir", "DEFAULT_PAGE_LIMIT", "MAX_PAGE_LIMIT"]
