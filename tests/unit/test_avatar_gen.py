"""Unit tests: W11 个性化像素角色生成系统 —— 引擎 + 持久化 + 路由.

覆盖任务书第 5 节验收点：

* **确定性**：同画像两次生成逐字节相同（matrix / palette / fingerprint）；
* **防重样**：3 组差异画像产出 3 个不同指纹；参数空间 ≥ 10⁶；
* **诚实标注**：缺项走中性默认并在 ``advisory`` 里如实说明「非你的真实数据」；
* **合成器结构**：24×32 恒定、8 层非空、≥16 色、跨组合不越界；
* **配色自检**：画布相邻色明度差达标（自动微调后二次校验为 0 违规）；
* **微调 + 还原**：改呈现指纹、不改底稿指纹、底稿签名保留、一键还原一致；
* **owner 隔离**：另一账号读不到（404），service 身份写入 403；
* **CSRF**：写操作缺 token 一律 401/403；
* **分享卡隐私**：未勾选字段一律不上卡；白名单外字段明确报错；
* **状态机**：草稿不得出分享卡；乐观锁过期 409。
"""

from __future__ import annotations

import itertools
from datetime import timezone
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
from find_yourself.services import avatar_gen as ag
from find_yourself.services.errors import ValidationFailed

LOCAL_TOKEN = "dev-token-secret-w11"

# 三组差异极大的画像：五行/星座/MBTI 三轴全不同
PORTRAIT_A = {
    "mbti": "ESTJ", "bazi_element": "金", "bazi_day_master": "庚",
    "sun_sign": "aries", "moon_sign": "libra", "asc_sign": "sagittarius",
    "name": "李雷", "mood": "sunny",
}
PORTRAIT_B = {
    "mbti": "ISFP", "bazi_element": "水", "bazi_day_master": "壬",
    "sun_sign": "pisces", "moon_sign": "capricorn", "asc_sign": "taurus",
    "name": "韩梅梅", "mood": "dreamy",
}
PORTRAIT_C = {
    "mbti": "ENFJ", "bazi_element": "木", "bazi_day_master": "甲",
    "sun_sign": "gemini", "moon_sign": "sagittarius", "asc_sign": "libra",
    "name": "小腾子", "mood": "calm",
}


# ======================================================================
# 一、引擎：确定性 / 防重样 / 参数空间
# ======================================================================
def test_engine_is_byte_identical_across_repeated_calls():
    a1 = ag.build_avatar(PORTRAIT_A)
    a2 = ag.build_avatar(PORTRAIT_A)
    assert a1["matrix"] == a2["matrix"]
    assert a1["palette"] == a2["palette"]
    assert a1["fingerprint"] == a2["fingerprint"]
    assert a1["params_fingerprint"] == a2["params_fingerprint"]


def test_three_distinct_portraits_produce_three_distinct_fingerprints():
    fps = {p["name"]: ag.build_avatar(p)["fingerprint"] for p in (PORTRAIT_A, PORTRAIT_B, PORTRAIT_C)}
    assert len(set(fps.values())) == 3, fps
    # 呈现也不能撞：矩阵逐个不同
    mats = [tuple(map(tuple, ag.build_avatar(p)["matrix"])) for p in (PORTRAIT_A, PORTRAIT_B, PORTRAIT_C)]
    assert len(set(mats)) == 3


def test_param_space_far_exceeds_one_million():
    assert ag.PARAM_SPACE_SIZE >= 1_000_000
    # 参数空间必须由真实常量算出，而不是写死的数字
    expected = (
        len(ag.HAIR_STYLES) * len(ag.HAIR_TONES) * len(ag.EYE_STYLES)
        * len(ag.MOUTH_STYLES) * len(ag.OUTFITS) * len(ag.SUN_HEADWEAR)
        * len(ag.ZODIAC_EMBLEMS) * len(ag.TEXTURES)
    )
    assert ag.PARAM_SPACE_SIZE == expected


def test_missing_portrait_fields_fall_back_to_neutral_and_say_so():
    out = ag.build_avatar({})
    advisory = out["advisory"]
    assert advisory["complete"] is False
    assert set(advisory["pending"]) == {
        "mbti", "bazi_element", "sun_sign", "moon_sign", "asc_sign",
    }
    # 诚实红线：必须明说「中性默认不是你的真实数据」
    assert "中性默认" in advisory["note"]
    assert "非你的真实数据" in advisory["note"]


def test_full_portrait_reports_complete():
    advisory = ag.build_avatar(PORTRAIT_A)["advisory"]
    assert advisory["complete"] is True
    assert advisory["pending"] == []


# ======================================================================
# 二、像素合成器：结构红线
# ======================================================================
@pytest.mark.parametrize("portrait", [PORTRAIT_A, PORTRAIT_B, PORTRAIT_C, {}])
def test_avatar_is_24x48_with_eight_non_empty_layers(portrait):
    out = ag.build_avatar(portrait)
    assert (out["width"], out["height"]) == (24, 48)
    assert tuple(out["layers"]) == ag.LAYER_NAMES
    assert len(ag.LAYER_NAMES) == 8
    for name, rows in out["layers"].items():
        assert len(rows) == 48, name
        assert all(len(r) == 24 for r in rows), name
        assert any(ch != "." for r in rows for ch in r), f"{name} 是空层"


@pytest.mark.parametrize("portrait", [PORTRAIT_A, PORTRAIT_B, PORTRAIT_C, {}])
def test_palette_has_at_least_sixteen_colors(portrait):
    assert len(ag.build_avatar(portrait)["palette"]) >= ag.MIN_PALETTE_COLORS


def test_every_matrix_glyph_maps_to_a_palette_key():
    """任何出现过的字符都必须能在 CHAR_KEYS 找到语义色，否则前端渲染成空洞。"""
    for portrait in (PORTRAIT_A, PORTRAIT_B, PORTRAIT_C, {}):
        out = ag.build_avatar(portrait)
        used = {ch for row in out["matrix"] for ch in row if ch != "."}
        assert used <= set(ag.CHAR_KEYS), used - set(ag.CHAR_KEYS)
        assert set(out["palette"]) == set(ag.CHAR_KEYS.values())


def test_compose_layers_never_out_of_bounds_across_dimension_sweep():
    """单维全覆盖 + 交叉抽样：任何参数组合都不得越界或漏色。"""
    axes = [
        list(ag.HAIR_STYLES), list(ag.HAIR_TONES), list(ag.EYE_STYLES),
        list(ag.MOUTH_STYLES), list(ag.OUTFITS), list(ag.SUN_HEADWEAR),
        list(ag.ZODIAC_EMBLEMS), list(ag.TEXTURES),
    ]
    base = [axis[0] for axis in axes]
    combos = [tuple(base[:i] + [v] + base[i + 1:]) for i, axis in enumerate(axes) for v in axis]
    combos += [tuple(ag._deterministic_rng(n) and axes[j][int(ag._deterministic_rng(n + j)() * len(axes[j]))]
                     for j in range(len(axes))) for n in range(50)]
    fp = ag.fingerprint(ag.build_portrait(PORTRAIT_A))
    for hs, ht, eye, mouth, oc, acc, emb, tex in combos:
        params = ag.AvatarParams(
            hair_style=hs, hair_tone=ht, palette_id="火", eye=eye, mouth=mouth,
            accessory=acc, outfit=oc, emblem=emb, texture=tex, height_scale=1.0,
            colors=ag._build_colors(ag.ELEMENT_PALETTES["火"], ht, "short_red", "火"),
            labels={"face_shape": "round", "hand_item_id": "lantern"},
            sources={}, missing=[], fingerprint=fp,
        )
        layers = ag.compose_layers(params)
        used = {ch for rows in layers.values() for r in rows for ch in r if ch != "."}
        assert used <= set(ag.CHAR_KEYS), (hs, ht, eye, mouth, oc, acc, emb, tex, used - set(ag.CHAR_KEYS))
        for name, rows in layers.items():
            assert len(rows) == 48 and all(len(r) == 24 for r in rows), name


def test_idle_frames_and_ground_shadow_exist():
    """任务书 §2.2：两帧待机呼吸 + 两帧行走 + 地面椭圆影。"""
    out = ag.build_avatar(PORTRAIT_A)
    shadow = out["layers"]["shadow"]
    assert any(ch != "." for r in shadow for ch in r)
    # 地面影必须在画布下部（40 行之后）
    assert any(ch != "." for row in shadow[40:] for ch in row)
    # 帧数据由前端从分层矩阵派生（见 api/avatar.ts deriveFrames），这里保证层够用
    assert "body" in out["layers"] and "hair" in out["layers"]


def test_face_features_land_inside_the_visible_face():
    """五官必须落在脸的可见区（防止头发盖脸/表情画到脸外）。"""
    frx, fry = ag.face_radii("round")
    hole = ag._face_hole_mask(frx, fry, 0)
    for eye in ag.EYE_STYLES:
        for mouth in ag.MOUTH_STYLES:
            rows = ag._draw_face(eye, mouth, "round")
            painted = [
                (x, y)
                for y, row in enumerate(rows)
                for x, ch in enumerate(row)
                if ch != "."
            ]
            assert painted, (eye, mouth)
            for x, y in painted:
                assert hole[y][x], f"{eye}/{mouth} 的五官画到脸外了 @({x},{y})"


# ======================================================================
# 三、配色自检（避免像素脏色）
# ======================================================================
@pytest.mark.parametrize("portrait", [PORTRAIT_A, PORTRAIT_B, PORTRAIT_C, {}])
def test_palette_harmony_passes_after_automatic_adjustment(portrait):
    palette = ag.build_avatar(portrait)["palette"]
    adjusted = ag.check_palette_harmony(palette)["adjusted"]
    final = ag.check_palette_harmony(adjusted)
    assert final["ok"], final["violations"]


def test_contrast_ratio_is_wcag_formula():
    assert ag.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert ag.contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)


# ======================================================================
# 四、微调 + AI 底稿一键还原
# ======================================================================
def test_tuning_changes_look_but_never_the_identity_fingerprint():
    base = ag.build_avatar(PORTRAIT_A)
    tuned = ag.build_avatar(PORTRAIT_A, overrides={"hair_style": "twin_tail", "hue_shift": 2})
    assert tuned["params_fingerprint"] != base["params_fingerprint"]
    assert tuned["fingerprint"] == base["fingerprint"]
    assert tuned["tuned"] is True and base["tuned"] is False


def test_ai_base_draft_is_always_recoverable():
    base = ag.build_avatar(PORTRAIT_A)
    base_signature = base["base_signature"]
    tuned = ag.build_avatar(PORTRAIT_A, overrides={"outfit": "robe", "eye": "sharp"})
    assert tuned["base_signature"] == base_signature
    # 一键还原 = 丢弃 overrides 重建
    restored = ag.build_avatar(PORTRAIT_A, overrides={})
    assert restored["matrix"] == base["matrix"]
    assert restored["params_fingerprint"] == base["params_fingerprint"]


def test_unknown_tuning_field_is_rejected_not_ignored():
    with pytest.raises(ValidationFailed) as exc:
        ag.build_avatar(PORTRAIT_A, overrides={"nope": 1})
    assert exc.value.code == "avatar_unknown_override"


def test_out_of_range_hue_shift_is_rejected():
    with pytest.raises(ValidationFailed):
        ag.build_avatar(PORTRAIT_A, overrides={"hue_shift": 9})


# ======================================================================
# 五、分享卡隐私
# ======================================================================
def test_share_card_only_contains_explicitly_checked_badges():
    card = ag.build_share_card(
        PORTRAIT_A, ["mbti", "element", "sun_sign", "name"], display_name="李雷"
    )
    fields = {b["field"] for b in card["badges"]}
    assert fields == {"mbti", "element", "sun_sign", "name"}
    assert "moon_sign" not in fields and "asc_sign" not in fields
    assert "moon_sign" in card["excluded_fields"]
    assert card["width"] == 720 and card["height"] == 960


def test_share_card_with_no_badges_leaks_nothing():
    card = ag.build_share_card(PORTRAIT_A, [])
    assert card["badges"] == []
    # 只保留产品标识与隐私声明，不含任何画像值
    blob = repr(card["badges"]) + repr(card["caption"])
    assert "ESTJ" not in blob and "aries" not in blob


def test_share_card_rejects_fields_outside_the_whitelist():
    with pytest.raises(ValidationFailed) as exc:
        ag.build_share_card(PORTRAIT_A, ["mbti", "raw_owner_id"])
    assert exc.value.code == "avatar_unknown_badge"


def test_share_card_short_code_tracks_the_tuned_look():
    plain = ag.build_share_card(PORTRAIT_A, ["mbti"])
    tuned = ag.build_share_card(PORTRAIT_A, ["mbti"], overrides={"hair_style": "curly"})
    assert plain["fingerprint_short"] != tuned["fingerprint_short"]
    assert tuned["base_fingerprint_short"] == plain["base_fingerprint_short"]


# ======================================================================
# 六、HTTP 层：鉴权 / CSRF / owner 隔离 / 状态机
# ======================================================================
def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def app(session_maker, tmp_path) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )
    return create_app(session_maker=session_maker, settings=settings)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_force_loopback(app)) as c:
        yield c


def _login(client: TestClient, *, owner: str) -> dict[str, str]:
    """真实多租户路径：注册 → 登录，拿到该 owner 自己的 session + CSRF。"""
    email = f"{owner}@w11.test"
    password = "W11-test-pass-12345"
    reg = client.post(
        "/auth/register",
        json={"email": email, "password": password, "consent_accepted": True},
    )
    assert reg.status_code == 200, reg.text
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    return _login(client, owner="owner-w11-a")

def _gen(client: TestClient, headers: dict, portrait=None, overrides=None):
    body: dict = {"portrait": portrait if portrait is not None else PORTRAIT_A}
    if overrides is not None:
        body["overrides"] = overrides
    return client.post("/api/avatar/generate", json=body, headers=headers)


def test_generate_returns_full_avatar_package(client: TestClient, headers: dict):
    r = _gen(client, headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["state"] == "draft"
    assert data["avatar"]["width"] == 24 and data["avatar"]["height"] == 48
    assert len(data["avatar"]["layers"]) == 8
    assert len(data["avatar"]["palette"]) >= 16
    assert data["advisory"]["complete"] is True
    # ID 由服务端生成，客户端不能自选
    assert data["id"].startswith("av_")
    # 契约护栏：前端微调面板读 data["avatar"]["params"][key]（hair_style 等），
    # 若 _pack 漏发 params，页面对「生成我的小人」后必崩且白屏。
    assert "params" in data["avatar"], "avatar.params 必须随包下发（前端微调面板依赖）"
    assert data["avatar"]["params"].get("hair_style"), "avatar.params.hair_style 必须非空"
    # 顶层 params 与包内 params 必须一致（同一份引擎结果，不许两处漂移）
    assert data["avatar"]["params"] == data["params"]


def test_generate_is_upsert_not_append(client: TestClient, headers: dict):
    first = _gen(client, headers).json()
    second = _gen(client, headers).json()
    assert first["id"] == second["id"]
    me = client.get("/api/avatar/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["id"] == first["id"]


def test_reaching_generate_resets_likeness_and_house_flag(client: TestClient, headers: dict):
    gen = _gen(client, headers).json()
    confirmed = client.put(
        "/api/avatar/confirm",
        json={"expected_version": gen["version"], "likeness_score": 9,
              "likeness_note": "挺像", "is_house_avatar": True},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["likeness_score"] == 9
    assert confirmed.json()["is_house_avatar"] is True
    # 重新生成 = 换了角色，旧的自评失效（评的是旧角色）。
    #
    # 但 is_house_avatar **不再被清空**：用户诉求是「首次进入可 DIY，
    # 确认之后全局锁定同一个角色」。若在这里清掉，微调一次小屋就 404，
    # 前端回退默认小人 —— 用户看到的就是「刚调完角色，进小屋却换了个人」。
    # state 退回 draft 表示「这次改动还没被点头」，与「是否投放小屋」是两回事。
    again = _gen(client, headers).json()
    assert again["state"] == "draft"
    assert again["likeness_score"] is None
    assert again["is_house_avatar"] is True

    # 红线仍在：重新生成后小屋必须还能取到角色（否则就是「微调一次小屋空了」）。
    house = client.get("/api/avatar/house", headers=headers)
    assert house.status_code == 200, house.text
    assert house.json()["fingerprint"] == again["params_fingerprint"]


def test_me_returns_404_before_any_generation(client: TestClient, headers: dict):
    r = client.get("/api/avatar/me", headers=headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "avatar_not_found"


def test_write_without_csrf_token_is_rejected(client: TestClient):
    r = client.post("/api/avatar/generate", json={"portrait": PORTRAIT_A})
    assert r.status_code in (401, 403), r.text


def test_cross_origin_write_is_rejected(client: TestClient, headers: dict):
    r = client.post(
        "/api/avatar/generate", json={"portrait": PORTRAIT_A},
        headers={**headers, "Origin": "http://evil.example"},
    )
    assert r.status_code in (401, 403), r.text


def test_owner_body_field_grants_no_authority(client: TestClient, headers: dict):
    """请求体里的 owner_id 无授权效力：不能替别人写档案。"""
    _gen(client, headers)
    r = client.post(
        "/api/avatar/generate",
        json={"portrait": PORTRAIT_B, "owner_id": "someone-else"},
        headers=headers,
    )
    assert r.status_code == 422, r.text  # extra=forbid 直接拒
    # 统一错误信封用的是 validation_failed（不是 FastAPI 默认的 request_validation_error）
    assert r.json()["error"]["code"] == "validation_failed"


def test_other_owner_cannot_read_my_avatar(app: FastAPI):
    with TestClient(_force_loopback(app)) as c1, TestClient(_force_loopback(app)) as c2:
        h1 = _login(c1, owner="owner-w11-a")
        h2 = _login(c2, owner="owner-w11-b")
        assert _gen(c1, h1).status_code == 200
        # 跨 owner 读 → 404（不泄露存在性），绝不是 200 也不是 500
        r = c2.get("/api/avatar/me", headers=h2)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "avatar_not_found"


def test_each_owner_gets_their_own_avatar(app: FastClient):
    with TestClient(_force_loopback(app)) as c1, TestClient(_force_loopback(app)) as c2:
        h1 = _login(c1, owner="owner-w11-a")
        h2 = _login(c2, owner="owner-w11-b")
        a = _gen(c1, h1, PORTRAIT_A).json()
        b = _gen(c2, h2, PORTRAIT_B).json()
        assert a["id"] != b["id"]
        assert a["fingerprint"] != b["fingerprint"]
        assert c1.get("/api/avatar/me", headers=h1).json()["fingerprint"] == a["fingerprint"]
        assert c2.get("/api/avatar/me", headers=h2).json()["fingerprint"] == b["fingerprint"]


def test_confirm_with_stale_version_conflicts(client: TestClient, headers: dict):
    gen = _gen(client, headers).json()
    r = client.put(
        "/api/avatar/confirm",
        json={"expected_version": gen["version"] + 5},
        headers=headers,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "avatar_version_conflict"


def test_likeness_score_out_of_range_is_rejected(client: TestClient, headers: dict):
    gen = _gen(client, headers).json()
    r = client.put(
        "/api/avatar/confirm",
        json={"expected_version": gen["version"], "likeness_score": 42},
        headers=headers,
    )
    assert r.status_code == 422


def test_share_card_requires_confirmed_avatar(client: TestClient, headers: dict):
    _gen(client, headers)
    r = client.post("/api/avatar/share-card", json={"badges": ["mbti"]}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "avatar_not_confirmed"


def test_share_card_after_confirm_respects_privacy(client: TestClient, headers: dict):
    gen = _gen(client, headers).json()
    client.put("/api/avatar/confirm", json={"expected_version": gen["version"]}, headers=headers)
    r = client.post(
        "/api/avatar/share-card",
        json={"badges": ["mbti", "element"], "display_name": "李雷"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    card = r.json()
    assert {b["field"] for b in card["badges"]} == {"mbti", "element"}
    assert card["fingerprint_short"] and card["brand"]["product"] == "Find Yourself"
    assert card["privacy_note"]


def test_share_card_rejects_non_whitelisted_badge_over_http(client: TestClient, headers: dict):
    gen = _gen(client, headers).json()
    client.put("/api/avatar/confirm", json={"expected_version": gen["version"]}, headers=headers)
    r = client.post(
        "/api/avatar/share-card", json={"badges": ["mbti", "owner_id"]}, headers=headers
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "avatar_unknown_badge"


def test_unknown_portrait_field_is_rejected_over_http(client: TestClient, headers: dict):
    r = _gen(client, headers, {**PORTRAIT_A, "blood_type": "O"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "avatar_unknown_portrait_field"


def test_house_endpoint_only_serves_confirmed_house_avatar(client: TestClient, headers: dict):
    # 未生成 → 404
    assert client.get("/api/avatar/house", headers=headers).status_code == 404
    gen = _gen(client, headers).json()
    # 草稿 → 仍 404（小屋不得渲染草稿）
    assert client.get("/api/avatar/house", headers=headers).status_code == 404
    client.put(
        "/api/avatar/confirm",
        json={"expected_version": gen["version"], "is_house_avatar": True},
        headers=headers,
    )
    r = client.get("/api/avatar/house", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["width"] == 24 and data["height"] == 48
    assert data["char_keys"] == ag.CHAR_KEYS
    assert len(data["matrix"]) == 48


def test_house_avatar_matches_me_matrix(client: TestClient, headers: dict):
    gen = _gen(client, headers).json()
    client.put("/api/avatar/confirm", json={"expected_version": gen["version"]}, headers=headers)
    me = client.get("/api/avatar/me", headers=headers).json()
    house = client.get("/api/avatar/house", headers=headers).json()
    assert house["matrix"] == me["avatar"]["matrix"]


def test_advisory_reaches_the_client_verbatim(client: TestClient, headers: dict):
    r = _gen(client, headers, {})
    assert r.status_code == 200
    advisory = r.json()["advisory"]
    assert advisory["complete"] is False
    assert "中性默认" in advisory["note"]


# ======================================================================
# 七、性别 / 年龄档契约（接口接受 ≠ 假装影响造型）
# ======================================================================
def test_age_band_is_accepted_normalized_and_echoed_back():
    """合法档位必须被接受、归一化、并如实回显标签。"""
    for key, cn in ag.AGE_BANDS.items():
        adv = ag.build_avatar({**PORTRAIT_A, "age_band": key})["advisory"]
        assert adv["age_band_label"] == cn, key
        # 收到就必须明说「不影响剪影」，不许默默吞掉
        assert "年龄档" in adv["notes"] and "不改变角色剪影" in adv["notes"]


def test_illegal_age_band_degrades_to_missing_not_guessed():
    """非法档位必须降级为缺项，绝不猜一个最接近的。"""
    adv = ag.build_avatar({**PORTRAIT_A, "age_band": "elderly_vip"})["advisory"]
    assert adv["age_band_label"] is None
    assert "未填写" in adv["notes"]


def test_advisory_notes_and_age_band_label_always_present():
    """两个键恒定存在——前端可以无分支读取，不会拿到 undefined。"""
    for portrait in (PORTRAIT_A, PORTRAIT_B, {}):
        adv = ag.build_avatar(portrait)["advisory"]
        assert isinstance(adv["notes"], str) and adv["notes"]
        assert "age_band_label" in adv


def test_gender_and_age_band_do_not_change_the_silhouette_matrix():
    """刻意不改剪影，因此像素必须逐字节相同 —— 这是**契约**，不是巧合。

    任务书把「可选性别/年龄档」列为输入项，同时把「1:1.2 Q 版头身比」列为硬要求；
    若让年龄档改头身比会违反 Q 版红线，若按性别分剪影是刻板印象。故两者都不改，
    并用本测试把这个决定钉死，防止后人"顺手加上"。
    """
    base = ag.build_avatar(PORTRAIT_A)["matrix"]
    for extra in (
        {"gender": "female"}, {"gender": "male"}, {"gender": "nonbinary"},
        {"age_band": "child"}, {"age_band": "senior"},
        {"gender": "female", "age_band": "child"},
    ):
        assert ag.build_avatar({**PORTRAIT_A, **extra})["matrix"] == base, extra


def test_service_identity_cannot_write_or_read_an_avatar():
    """service 身份必须被服务层拒绝（403），不能只靠路由挡住。"""
    from find_yourself.services.actor import Actor
    from find_yourself.services.avatar_profile import AvatarProfileService
    from find_yourself.services.errors import PermissionDenied

    svc = Actor.service("svc-1", "worker", ["avatar"])
    service = AvatarProfileService.__new__(AvatarProfileService)
    for call in (
        lambda: service.generate(svc, portrait={}),
        lambda: service.confirm(svc, expected_version=1),
        lambda: service.me(svc),
        lambda: service.share_card(svc, badges=[]),
        lambda: service.house_avatar(svc),
    ):
        with pytest.raises(PermissionDenied) as exc:
            call()
        assert exc.value.http_status == 403
        assert exc.value.code == "owner_only"


# ======================================================================
# 八、迁移与前端契约
# ======================================================================
def test_migration_0020_is_reachable_from_the_single_head():
    """不变量断言（主控纠错 2026-10-04）：迁移链必须单头且 0020 可达。

    原先硬编码断言父节点为 0015——多任务并行时会与既有分支撞多 head；
    改为断言拓扑不变量，任何后续新增迁移只要保持单头即可通过。
    """
    import re
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    graph: dict[str, str | None] = {}
    for f in versions.glob("0*.py"):
        text = f.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "([^"]+)"', text, re.M)
        down = re.search(r'^down_revision: str \| None = "([^"]+)"', text, re.M)
        if rev:
            graph[rev.group(1)] = down.group(1) if down else None

    parents = {v for v in graph.values() if v}
    heads = sorted(r for r in graph if r not in parents)
    assert len(heads) == 1, f"迁移链必须单头，当前 heads={heads}"

    # 0020 必须在 head 的祖先链上
    chain = []
    cur: str | None = heads[0]
    while cur:
        chain.append(cur)
        cur = graph.get(cur)
    assert "0020_avatar_profiles" in chain, f"0020 不在 head 链上: {chain}"


def test_migration_0020_upgrade_and_downgrade_round_trip():
    """真实跑一遍 0020 的 upgrade/downgrade，不只是读源码。

    用内存 SQLite 直连 alembic 的 op，验证：建表成功 → 表与约束都在 →
    downgrade 删除 → 再 upgrade 可重建（幂等）。
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from pathlib import Path
    import importlib.util
    from sqlalchemy import create_engine, inspect, text as sa_text

    path = (
        Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0020_avatar_profiles.py"
    )
    spec = importlib.util.spec_from_file_location("m0020", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    eng = create_engine("sqlite://")
    with eng.connect() as conn:
        ctx = MigrationContext.configure(conn)
        # alembic 的 `op` 是模块级代理，必须用官方上下文管理器绑定本次连接，
        # 否则迁移里的 `op.create_table` 会落到默认连接上（实测静默建到别的库）。
        with Operations.context(ctx):
            mod.upgrade()  # type: ignore[name-defined]
            names = set(inspect(conn).get_table_names())
            assert "avatar_profiles" in names
            cols = {c["name"] for c in inspect(conn).get_columns("avatar_profiles")}
            assert {"owner_id", "state", "portrait", "params", "overrides",
                    "fingerprint", "params_fingerprint", "engine_version",
                    "likeness_score", "likeness_note", "is_house_avatar",
                    "version", "created_at", "updated_at"} <= cols
            idx = {i["name"] for i in inspect(conn).get_indexes("avatar_profiles")}
            uq = {u["name"] for u in inspect(conn).get_unique_constraints("avatar_profiles")}
            assert "ix_avatar_profiles_owner" in idx
            assert "uq_avatar_profile_owner" in uq
            # 约束真的生效：非法 state 必须被 CHECK 拒绝
            from sqlalchemy.exc import IntegrityError
            with pytest.raises(IntegrityError):
                conn.execute(sa_text(
                    "INSERT INTO avatar_profiles (owner_id, state, created_at, updated_at) "
                    "VALUES ('o1', 'bogus_state', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ))
            # downgrade → 表消失；再 upgrade → 可重建（幂等）
            mod.downgrade()  # type: ignore[name-defined]
            assert "avatar_profiles" not in set(inspect(conn).get_table_names())
            mod.upgrade()  # type: ignore[name-defined]
            assert "avatar_profiles" in set(inspect(conn).get_table_names())


def test_char_keys_are_the_shared_render_contract():
    """前端 cabinPixels 按同一张表查色；引擎必须把表一起下发。"""
    out = ag.build_avatar(PORTRAIT_A)
    assert set(ag.CHAR_KEYS.values()) == set(out["palette"])
    # 轮廓光与手持物必须有专属色键（曾经 rim_light 无对应色导致轮廓光不可见）
    assert "rim_light" in out["palette"]
    assert "hand_item" in out["palette"]
    assert "x" in ag.CHAR_KEYS and "g" in ag.CHAR_KEYS
