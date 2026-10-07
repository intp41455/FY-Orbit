"""市场评分与排位引擎（A-工具市场-03 · P12）。

提供插件与模板市场的评分、评语存储、星级分布统计与贝叶斯加权排位分计算。
核心目标：「好用的自然被顶上来」，同时保持冷启动平滑与防刷分。

数据落点：默认本地持久化 SQLite ``.runtime/marketplace_ratings.db``
（环境变量 ``FY_RATINGS_DB_PATH`` 可覆盖，测试传入 ``:memory:`` 即可完全隔离）。
零跨包锁冲突、零 alembic 迁移编号依赖。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ValidationFailed

#: 贝叶斯平滑先验参数：先验均值 m=3.0（中立），先验置信权重 C=5
PRIOR_RATING_MEAN = 3.0
PRIOR_RATING_WEIGHT = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bayesian_score(avg_rating: float, count: int) -> float:
    """计算贝叶斯加权排位得分（好用被顶上来）。

    公式: (C * m + avg * n) / (C + n)
    当评价较少时收敛于中立基准 3.0；当评价增多且高分时，得分逼近实际高分并占据前列。
    """
    if count <= 0:
        return PRIOR_RATING_MEAN
    score = (PRIOR_RATING_WEIGHT * PRIOR_RATING_MEAN + avg_rating * count) / (
        PRIOR_RATING_WEIGHT + count
    )
    return round(score, 3)


class MarketplaceRatingStore:
    """SQLite 本地轻量持久化评分存储。支持并发锁保护与内存测试模式。"""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = os.environ.get("FY_RATINGS_DB_PATH", ".runtime/marketplace_ratings.db")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                with conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS marketplace_ratings (
                            id TEXT PRIMARY KEY,
                            target_type TEXT NOT NULL,
                            target_id TEXT NOT NULL,
                            actor_id TEXT NOT NULL,
                            rating REAL NOT NULL,
                            comment TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(target_type, target_id, actor_id)
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_mkt_target
                        ON marketplace_ratings(target_type, target_id)
                    """)
            finally:
                conn.close()

    def rate(
        self,
        target_type: str,
        target_id: str,
        actor_id: str,
        rating: float | int,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """为目标提交或更新评分（1.0 ~ 5.0）。"""
        if not isinstance(rating, (int, float)) or isinstance(rating, bool):
            raise ValidationFailed("rating_invalid", "评分必须是 1 到 5 之间的数字")
        rating_f = float(rating)
        if rating_f < 1.0 or rating_f > 5.0:
            raise ValidationFailed("rating_out_of_range", "评分必须在 1.0 到 5.0 星之间")

        comment_clean = comment.strip() if comment else None
        if comment_clean and len(comment_clean) > 1000:
            raise ValidationFailed("comment_too_long", "评语不能超过 1000 字")

        now = _now_iso()
        record_id = str(uuid.uuid4())

        with self._lock:
            conn = self._get_conn()
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO marketplace_ratings (
                            id, target_type, target_id, actor_id, rating, comment, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(target_type, target_id, actor_id) DO UPDATE SET
                            rating = excluded.rating,
                            comment = excluded.comment,
                            updated_at = excluded.updated_at
                    """, (record_id, target_type, target_id, actor_id, rating_f, comment_clean, now, now))
            finally:
                conn.close()

        summary = self.get_summary(target_type, target_id)
        return {
            "target_type": target_type,
            "target_id": target_id,
            "actor_id": actor_id,
            "rating": rating_f,
            "comment": comment_clean,
            "updated_at": now,
            "summary": summary,
        }

    def get_summary(self, target_type: str, target_id: str) -> dict[str, Any]:
        """获取目标的综合评分摘要（平均星级、总次数、贝叶斯排位分、星级分布）。"""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT
                        AVG(rating) as avg_rating,
                        COUNT(*) as count
                    FROM marketplace_ratings
                    WHERE target_type = ? AND target_id = ?
                """, (target_type, target_id))
                row = cur.fetchone()
                avg = float(row["avg_rating"]) if row and row["avg_rating"] is not None else 0.0
                count = int(row["count"]) if row and row["count"] is not None else 0

                # 星级分布 (1..5)
                cur.execute("""
                    SELECT
                        CAST(ROUND(rating) AS INT) as star,
                        COUNT(*) as star_count
                    FROM marketplace_ratings
                    WHERE target_type = ? AND target_id = ?
                    GROUP BY star
                """, (target_type, target_id))
                dist = {str(i): 0 for i in range(1, 6)}
                for drow in cur.fetchall():
                    s = str(drow["star"])
                    if s in dist:
                        dist[s] = int(drow["star_count"])

                score = bayesian_score(avg, count)
                return {
                    "average_rating": round(avg, 1),
                    "rating_count": count,
                    "score": score,
                    "distribution": dist,
                }
            finally:
                conn.close()

    def get_ratings(
        self, target_type: str, target_id: str, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """获取目标的分页评价列表与摘要。"""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT COUNT(*) as total
                    FROM marketplace_ratings
                    WHERE target_type = ? AND target_id = ?
                """, (target_type, target_id))
                total = int(cur.fetchone()["total"])

                cur.execute("""
                    SELECT id, actor_id, rating, comment, created_at, updated_at
                    FROM marketplace_ratings
                    WHERE target_type = ? AND target_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """, (target_type, target_id, limit, offset))
                items = [
                    {
                        "id": r["id"],
                        "actor_id": r["actor_id"],
                        "rating": float(r["rating"]),
                        "comment": r["comment"],
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    }
                    for r in cur.fetchall()
                ]
            finally:
                conn.close()

        summary = self.get_summary(target_type, target_id)
        return {
            "summary": summary,
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_user_rating(self, target_type: str, target_id: str, actor_id: str) -> dict[str, Any] | None:
        """获取特定用户的评价。"""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, actor_id, rating, comment, created_at, updated_at
                    FROM marketplace_ratings
                    WHERE target_type = ? AND target_id = ? AND actor_id = ?
                """, (target_type, target_id, actor_id))
                r = cur.fetchone()
                if not r:
                    return None
                return {
                    "id": r["id"],
                    "actor_id": r["actor_id"],
                    "rating": float(r["rating"]),
                    "comment": r["comment"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            finally:
                conn.close()

    def batch_summaries(self, target_type: str, target_ids: list[str]) -> dict[str, dict[str, Any]]:
        """批量获取评分摘要。"""
        if not target_ids:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for tid in target_ids:
            result[tid] = self.get_summary(target_type, tid)
        return result


#: 全局默认评分存储单例
_default_store: MarketplaceRatingStore | None = None


def get_rating_store() -> MarketplaceRatingStore:
    global _default_store
    if _default_store is None:
        _default_store = MarketplaceRatingStore()
    return _default_store
