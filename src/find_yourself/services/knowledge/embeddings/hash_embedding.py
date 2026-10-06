"""确定性哈希嵌入（离线零依赖 · 默认 provider，补齐包2 · A-向量库-07①）。

原理：符号特征哈希（signed hashing trick）。对文本分词（CJK 单字+二元组、
拉丁词，剔除固定虚词表），每个 token 用 ``blake2b`` 稳定散列到 ``dim`` 维桶中
的 ``n_hash`` 个位置，按散列位赋予 ±1 符号并累加词频，再作 ``1+log`` 词频压缩
与 L2 归一化。

三个设计决定（都有数据支撑，见交付报告）：

* **确定性**：散列用 ``hashlib.blake2b``（跨进程/跨机器稳定）。Python 内建
  ``hash()`` 对 str 有进程级随机化（PYTHONHASHSEED），绝对不能用。
* **±1 符号**：无符号多重哈希的碰撞噪声是**正偏**的——无关文本的余弦相似度
  系统性漂高（dim=256/n_hash=3 实测可到 0.37，足以穿透任何 sane 的召回下限）。
  同一 token 双方符号一致（信号相干叠加），无关 token 符号随机（碰撞噪声
  零均值互相抵消），噪声底回落到 ~0.05 量级。
* **虚词表**：CJK 功能字（的了是在…）几乎出现在所有长文本里，是 bag-of-words
  相似度的主要假阳性来源；固定小表剔除，确定性不受影响。

它不是语义模型：相似度 ≈ 内容词形重叠的余弦化。相比词法路（FTS5/LIKE +
``score_chunk``）它有两点真实差异：(1) 不受 ``MIN_QUERY_COVERAGE`` 词法护栏的
硬截断——近义表述共享 ~30% 词形时词法路判 0，向量路仍有正分；(2) 分数连续，
与词法路做 RRF 融合时能提供第二意见。**长度敏感性**：超长文本会让桶饱和、
区分度下降，生产语料建议配远程嵌入（``FIND_YOURSELF_KB_EMBEDDING=openai``），
接口不变。远程嵌入可用时由调用方切换即可。
"""

from __future__ import annotations

import hashlib
import math
import os
import re

from .base import EmbeddingProvider

DEFAULT_HASH_DIM = 512
MIN_HASH_DIM = 16
MAX_HASH_DIM = 8192
DEFAULT_N_HASH = 2

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-z0-9_]+")

#: 固定虚词表（确定性硬编码，不做任何统计拟合）：单字功能词 + 高频功能 bigram。
#: 这是质量护栏不是语义判断——剔除后剩余的是内容词形。
_STOP_TOKENS = frozenset({
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没", "看",
    "好", "这", "那", "啊", "吧", "吗", "呢", "嘛", "呀", "哦", "之", "与",
    "及", "或", "但", "把", "被", "让", "给", "向", "从", "对", "以", "为",
    "么", "什", "怎", "样", "个", "中", "等", "们",
    "什么", "怎么", "这个", "那个", "这样", "那样", "可以", "我们", "你们",
    "他们", "自己", "没有", "一个", "一些", "一下", "因为", "所以", "但是",
    "然后", "还有", "以及", "如果", "对于", "关于", "通过", "还是", "只是",
})


def hash_tokenize(text: str) -> list[str]:
    """嵌入侧分词：拉丁词 + CJK 单字与二元组，剔除虚词（与检索分词独立）。"""
    lowered = (text or "").lower()
    tokens: list[str] = [t for t in _LATIN_RE.findall(lowered) if t not in _STOP_TOKENS]
    for run in _CJK_RE.findall(lowered):
        grams = [run[i : i + 2] for i in range(len(run) - 1)]
        for tok in (*run, *grams):
            if tok and tok not in _STOP_TOKENS:
                tokens.append(tok)
    return tokens


def _bucket_and_sign(token: str, salt: int, dim: int) -> tuple[int, float]:
    """token → (桶下标, ±1 符号)（blake2b，稳定）。"""
    digest = hashlib.blake2b(f"{salt}:{token}".encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if value >> 60 & 1 else -1.0


class HashingEmbedding(EmbeddingProvider):
    """确定性符号哈希嵌入。维度可配（构造参数 > 环境变量 > 默认 512）。"""

    name = "hash"

    def __init__(self, dim: int | None = None, *, n_hash: int | None = None):
        if dim is None:
            raw = os.getenv("FIND_YOURSELF_KB_HASH_DIM", "")
            try:
                dim = int(raw) if raw else DEFAULT_HASH_DIM
            except ValueError:
                dim = DEFAULT_HASH_DIM
        dim = int(dim)
        if not MIN_HASH_DIM <= dim <= MAX_HASH_DIM:
            raise ValueError(f"hash embedding dim must be in [{MIN_HASH_DIM}, {MAX_HASH_DIM}], got {dim}")
        self.dim = dim
        if n_hash is None:
            raw = os.getenv("FIND_YOURSELF_KB_HASH_N", "")
            try:
                n_hash = int(raw) if raw else DEFAULT_N_HASH
            except ValueError:
                n_hash = DEFAULT_N_HASH
        self.n_hash = max(1, int(n_hash))

    def is_available(self) -> bool:
        return True  # 纯 Python + hashlib，永远可用

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    # -- internals ----------------------------------------------------------- #
    def _embed(self, text: str) -> list[float]:
        counts: dict[int, float] = {}
        for token in hash_tokenize(text):
            for salt in range(self.n_hash):
                idx, sign = _bucket_and_sign(token, salt, self.dim)
                counts[idx] = counts.get(idx, 0.0) + sign
        vec = [0.0] * self.dim
        if not counts:
            return vec  # 空文本/纯虚词 → 零向量（相似度 0，检索层下限过滤）
        for idx, c in counts.items():
            if c == 0:  # ±1 完全相消的桶不携带信息，跳过（log(0) 无定义）
                continue
            magnitude = abs(c)
            vec[idx] = (1.0 + math.log(magnitude)) * (1.0 if c > 0 else -1.0)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
