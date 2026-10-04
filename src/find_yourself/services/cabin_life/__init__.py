"""像素风生活模拟 · **包 B（系统与内容）纯逻辑层**。

对应分包说明书 `docs/handoff-tasks/15-像素风生活模拟-分包施工说明书-给Antigravity与freebuff.md`
§5「包 B · 系统与内容」。按 §8-D 的排期，本次交付的是**数据层**（不碰
`cabinScene.ts` / `cabinPixelArt.ts`，那两个文件归 A 包）。

模块地图
--------
======================  ====================================================
模块                任务
======================  ====================================================
``rng.py``           公用：确定性随机（同 seed → 同结果，可复算的地基）
``themes.py``        主题注册表：现有 5 背景 + 规范四大主题（两类全都要）
``clock.py``         B9  昼夜 + 天气（含对采集/经营的真实影响）
``npcs.py``          B7  ≥10 NPC/图、作息表、好感 0-10 心、分时段对话、委托
``shop.py``          B8  经营：进货/定价/物价波动/升级/日结算
``crafting.py``      B3  配方表 + 技能解锁 + 材料校验
``quests.py``        B6  主线/支线 + 任务日志 + 头顶感叹号
``interaction.py``   B1/B2  靠近提示 + 八大类采集动作
``world.py``         B10 160×100 格地图生成 / 秘密区域 / 隐藏宝箱 / 随机事件 / 寻路
``build.py``         B4  家具目录 / 网格吸附 / 旋转 / 重叠 / 门口禁放 / 装修评分
======================  ====================================================

统一约定（诚实原则 §2-1）
------------------------
* **零 IO、零时钟、零全局随机**：全部纯函数，同输入必同输出，测试可逐条断言公式；
* 未知 id（主题/天气/货品/配方/任务/NPC）一律 **抛错**，绝不静默回落默认值——
  静默回落会让「未实现」看起来像「已实现」；
* 任何「不足 / 不满 / 超限」都有**明确原因文案**，不伪造成功、不美化数字；
* 本层**不碰** A1 冻结的常量（`VIRTUAL_W/H`、`TILE` 等）与美术矩阵。

尚未包含（诚实列出，见交付报告「未尽事项」）
------------------------------------------
B5 房屋进入，以及 B11 存档的持久化与 HTTP 通道。
B10（`world.py`）与 B4（`build.py`）已落**数据层**；两者的**渲染段**
（镜头跟随 / 无缝滚动 / 瓦片绘制 / 家具绘制）按 §2.5 串行点要改 `cabinScene.ts`，
等 A5 收工后再做。
"""

from __future__ import annotations

from . import build, clock, crafting, interaction, npcs, quests, rng, shop, themes, world
from .clock import GameClock, roll_weather, weather_of
from .themes import THEME_IDS, ThemeDef, get_theme

__all__ = [
    "rng",
    "themes",
    "clock",
    "npcs",
    "shop",
    "crafting",
    "quests",
    "interaction",
    "world",
    "build",
    "GameClock",
    "roll_weather",
    "weather_of",
    "THEME_IDS",
    "ThemeDef",
    "get_theme",
]