"""N6 RAG 深化：混合检索质量评测与召回对比（对标 FastGPT 评测形态）。

语料 8 篇 × 查询 12 条（5 exact + 7 paraphrase），每查询 1 个相关文档，
口径 recall@3；同时跑 lexical / vector / hybrid(rrf) / hybrid(weighted) 四臂：

* exact 集：四臂必须全中（混合不劣化已达部分）；
* rescue 集：词法护栏（MIN_QUERY_COVERAGE）必 miss，向量/混合必救回（互补成立）；
* 总表：hybrid(rrf) 总召回 ≥ lexical 且 ≥ vector。

评测短语经调参探针选入 rescue 区（同形改写、共享 2-4 字但过不了词法覆盖闸），
见 ``rag_tune.py`` 探针输出。评测表打印到 stdout 供交付报告引用。
"""

from __future__ import annotations

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.knowledge.ingest import KnowledgeIngestService
from find_yourself.services.knowledge.search import KnowledgeSearchService

OWNER = "owner-1"
K = 3

CORPUS = {
    "cabin.md": "# 小屋\n\n小屋里可以耕种胡萝卜，收获后在野炊锅里烹饪成蔬菜汤。\n",
    "fishing.md": "# 垂钓\n\n去溪水边垂钓可以钓到鲫鱼，鲫鱼能卖金币。\n",
    "avatar.md": "# 像素小人\n\n像素小人的发型可以在角色工坊里更换，发色一共有二十种。\n",
    "tarot.md": "# 塔罗\n\n塔罗牌的大阿卡那一共有二十二张，愚者是零号牌。\n",
    "budget.md": "# 预算\n\n每个任务的预算上限是零点五美元，每月总上限十美元。\n",
    "overtime.md": "# 加班\n\n连续加班三周后，我开始掉头发，医生建议早睡。\n",
    "sourdough.md": "# 酸面包\n\n酸面包的 starter 需要每天喂养一次全麦粉。\n",
    "subway.md": "# 地铁\n\n早高峰的地铁三号线非常拥挤，建议提前一站上车。\n",
}

EXACT_CASES = [
    ("小屋耕种胡萝卜烹饪", "cabin.md"),
    ("溪水边垂钓鲫鱼卖金币", "fishing.md"),
    ("角色工坊更换发型发色", "avatar.md"),
    ("塔罗大阿卡那二十二张", "tarot.md"),
    ("任务预算上限零点五", "budget.md"),
]

RESCUE_CASES = [
    ("种菜煮汤要去哪里", "cabin.md"),
    ("美元额度用完了吗", "budget.md"),
    ("捏脸怎么换发型", "avatar.md"),
    ("鲫鱼值多少钱", "fishing.md"),
    ("金币怎么赚", "fishing.md"),
    ("愚者牌什么意思", "tarot.md"),
    ("面包种饿死了", "sourdough.md"),
]

ARMS = ("lexical", "vector", "hybrid_rrf", "hybrid_weighted")


def _seed_corpus(session):
    ing = KnowledgeIngestService(session, AuditService(session))
    actor = Actor.owner(OWNER)
    for name, text in CORPUS.items():
        ing.ingest_text(actor, owner_id=OWNER, name=name, text=text)


def _run_arms(service, actor, query):
    out = {}
    lex = service.search(actor, owner_id=OWNER, query=query, top_k=K, mode="lexical")
    out["lexical"] = [h["doc_name"] for h in lex]
    vec = service.search(actor, owner_id=OWNER, query=query, top_k=K, mode="vector")
    out["vector"] = [h["doc_name"] for h in vec]
    rrf = service.search(actor, owner_id=OWNER, query=query, top_k=K,
                         mode="hybrid", fusion="rrf")
    out["hybrid_rrf"] = [h["doc_name"] for h in rrf]
    weighted = service.search(actor, owner_id=OWNER, query=query, top_k=K,
                              mode="hybrid", fusion="weighted")
    out["hybrid_weighted"] = [h["doc_name"] for h in weighted]
    return out


def test_hybrid_quality_eval_recall_table(session, capsys):
    _seed_corpus(session)
    service = KnowledgeSearchService(session)
    actor = Actor.owner(OWNER)
    rows = []
    for label, cases in (("exact", EXACT_CASES), ("rescue", RESCUE_CASES)):
        for query, target in cases:
            arms = _run_arms(service, actor, query)
            rows.append({
                "query": query, "type": label, "target": target,
                **{arm: int(target in arms[arm]) for arm in ARMS},
            })
    print("\n== N6 混合检索质量评测（recall@3，hash 嵌入 + sqlite-vec，离线）==")
    print(f"{'query':<20} {'type':<7} {'lex':>4} {'vec':>4} {'rrf':>4} {'wtd':>4}")
    for r in rows:
        print(f"{r['query']:<20} {r['type']:<7} {r['lexical']:>4} {r['vector']:>4} "
              f"{r['hybrid_rrf']:>4} {r['hybrid_weighted']:>4}")
    totals = {arm: sum(r[arm] for r in rows) for arm in ARMS}
    print(f"TOTAL/{len(rows)}: " + " ".join(f"{arm}={totals[arm]}" for arm in ARMS))

    # 1. exact 集：四臂全中（混合不劣化）。
    for r in rows:
        if r["type"] == "exact":
            assert all(r[arm] == 1 for arm in ARMS), f"exact 缺口：{r}"
    # 2. rescue 集：词法必 miss，向量/混合必救回。
    for r in rows:
        if r["type"] == "rescue":
            assert r["lexical"] == 0, f"词法护栏应拦截近义改写：{r['query']}"
            assert r["vector"] == 1, f"向量路应救回：{r['query']}"
            assert r["hybrid_rrf"] == 1, f"混合(rrf)应救回：{r['query']}"
            assert r["hybrid_weighted"] == 1, f"混合(weighted)应救回：{r['query']}"
    # 3. 总表：混合不劣于任一单路。
    assert totals["hybrid_rrf"] >= totals["lexical"]
    assert totals["hybrid_rrf"] >= totals["vector"]
    assert totals["hybrid_weighted"] >= totals["lexical"]
    assert totals["hybrid_weighted"] >= totals["vector"]
