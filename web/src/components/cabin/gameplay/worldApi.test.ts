import { describe, expect, it } from 'vitest';
import fixture from './__fixtures__/worldMap.sample.json';
import { TILE, VIRTUAL_H, VIRTUAL_W, WORLD } from '../cabinConfig';
import {
  cameraXFor,
  cameraYFor,
  chestValueLine,
  eventLine,
  gradeOf,
  isTerrainId,
  manhattan,
  mapSizeLine,
  nearbySecrets,
  pixelToTileCol,
  TERRAIN_FALLBACK_LABELS,
  TERRAIN_IDS,
  terrainLabel,
  terrainShareLines,
  tileRect,
  tileToPixelX,
  visibleChests,
  visibleTileCount,
  visibleTiles,
  type Chest,
  type EventOutcome,
  type SecretArea,
  type WorldBlock,
} from './worldApi';

/**
 * B10 前端契约测试。
 *
 * fixture 是**后端真跑产出的快照**（`cabin_life.world.generate_map`），
 * 价值在于：后端改了字段名 / 尺寸 / 事件集而前端没跟上时，只有这条测试会红。
 * 另有一组断言把「A1 冻结常量」与「后端地图尺寸」钉在一起 —— 两端一旦漂移就红。
 */

const world = fixture.world as unknown as WorldBlock;
const secrets = fixture.secrets as unknown as SecretArea[];
const chests = fixture.chests as unknown as Chest[];
const events = fixture.events as unknown as EventOutcome[];

describe('B10 与 A1 冻结常量对齐', () => {
  it('后端列数 = WORLD.width / TILE', () => {
    expect(world.cols).toBe(WORLD.width / TILE);
  });

  it('后端 tile 字段就是 A1 的 TILE', () => {
    expect(world.tile).toBe(TILE);
  });

  it('后端像素宽 = 列数 × TILE', () => {
    expect(world.width_px).toBe(world.cols * TILE);
    expect(world.height_px).toBe(world.rows * TILE);
  });

  it('地图不小于规范下限 100×100', () => {
    expect(world.cols).toBeGreaterThanOrEqual(100);
    expect(world.rows).toBeGreaterThanOrEqual(100);
  });

  it('世界像素宽至少容得下 8 屏虚拟宽度', () => {
    expect(world.width_px).toBeGreaterThanOrEqual(VIRTUAL_W * 8);
  });
});

describe('地形表契约', () => {
  it('fixture 的地形标签覆盖 TERRAIN_IDS', () => {
    const labels = fixture.terrain_labels as Record<string, string>;
    for (const id of TERRAIN_IDS) {
      expect(labels[id]).toBeTruthy();
    }
  });

  it('fallback 标签集与 TERRAIN_IDS 同长度', () => {
    expect(Object.keys(TERRAIN_FALLBACK_LABELS)).toHaveLength(TERRAIN_IDS.length);
  });

  it('isTerrainId 接受合法 / 拒绝非法', () => {
    expect(isTerrainId('grass')).toBe(true);
    expect(isTerrainId('lava')).toBe(false);
    expect(isTerrainId(7)).toBe(false);
  });

  it('直方图的键全部是已知地形', () => {
    for (const key of Object.keys(world.histogram)) {
      expect(isTerrainId(key)).toBe(true);
    }
  });

  it('直方图之和 = 地图总格数', () => {
    const sum = Object.values(world.histogram).reduce((a, b) => a + b, 0);
    expect(sum).toBe(world.cols * world.rows);
  });

  it('terrainLabel 兜底但不瞎编', () => {
    expect(terrainLabel('grass')).toBe('草地');
    expect(terrainLabel('lava')).toBe('未知地形');
  });
});

describe('像素换算', () => {
  it('tileToPixelX 乘的是 A1 的 TILE', () => {
    expect(tileToPixelX(69)).toBe(69 * TILE);
  });

  it('pixelToTileCol 与 tileToPixelX 互逆', () => {
    for (const col of [0, 1, 20, 69, 159]) {
      expect(pixelToTileCol(tileToPixelX(col))).toBe(col);
    }
  });

  it('tileRect 尺寸恒等于 TILE', () => {
    const r = tileRect([10, 20]);
    expect(r).toEqual({ x: 10 * TILE, y: 20 * TILE, width: TILE, height: TILE });
  });

  it('manhattan 是格距离不是像素距离', () => {
    expect(manhattan([0, 0], [3, 4])).toBe(7);
  });
});

describe('视口裁剪（无缝滚动的渲染预算）', () => {
  it('相机在原点时从 col 0 / row 0 开始', () => {
    const w = visibleTiles(0, 0, VIRTUAL_W, VIRTUAL_H, world);
    expect(w.col0).toBe(0);
    expect(w.row0).toBe(0);
  });

  it('右缘闭区间：640 宽盖 col 0..20（21 格）', () => {
    const w = visibleTiles(0, 0, VIRTUAL_W, VIRTUAL_H, world);
    // 640 / 32 = 20，第 20 格起点恰在视口右缘；闭区间多画一格保证不漏缝
    expect(w.col1).toBe(VIRTUAL_W / TILE);
    expect(visibleTileCount(w)).toBe(21 * 12);
  });

  it('不超过地图右界', () => {
    const w = visibleTiles(9e9, 9e9, VIRTUAL_W, VIRTUAL_H, world);
    expect(w.col1).toBeLessThanOrEqual(world.cols - 1);
    expect(w.row1).toBeLessThanOrEqual(world.rows - 1);
  });

  it('一屏格数在合理预算内（约 21×13）', () => {
    const w = visibleTiles(1000, 500, VIRTUAL_W, VIRTUAL_H, world);
    expect(visibleTileCount(w)).toBeLessThan(25 * 16);
    expect(visibleTileCount(w)).toBeGreaterThan(20 * 11);
  });

  it('相机右移时窗口右移且宽度不变', () => {
    const a = visibleTiles(0, 0, VIRTUAL_W, VIRTUAL_H, world);
    const b = visibleTiles(TILE * 10, 0, VIRTUAL_W, VIRTUAL_H, world);
    expect(b.col0 - a.col0).toBe(10);
    expect(visibleTileCount(b)).toBe(visibleTileCount(a));
  });
});

describe('镜头跟随（纯函数，供渲染段消费）', () => {
  it('人物在左段时相机不越 0', () => {
    expect(cameraXFor(100, VIRTUAL_W)).toBe(0);
  });

  it('人物在右段时相机钳在 WORLD.width - 视口宽', () => {
    expect(cameraXFor(WORLD.width - 10, VIRTUAL_W)).toBe(WORLD.width - VIRTUAL_W);
  });

  it('相机单调不减', () => {
    let prev = -1;
    for (let x = 0; x <= WORLD.width; x += 100) {
      const cam = cameraXFor(x, VIRTUAL_W);
      expect(cam).toBeGreaterThanOrEqual(prev);
      prev = cam;
    }
  });

  it('纵向相机钳在 0 .. 高度-视口高', () => {
    expect(cameraYFor(0, VIRTUAL_H, world)).toBe(0);
    expect(cameraYFor(1e9, VIRTUAL_H, world)).toBe(world.height_px - VIRTUAL_H);
  });
});

describe('隐藏宝箱契约', () => {
  it('每主题每档都有表', () => {
    const counts = fixture.chest_counts as Record<string, number[]>;
    expect(Object.keys(counts)).toHaveLength(9);
  });

  it('宝箱 id 唯一', () => {
    expect(new Set(chests.map((c) => c.id)).size).toBe(chests.length);
  });

  it('宝箱坐标都在地图内', () => {
    for (const c of chests) {
      expect(c.tile[0]).toBeLessThan(world.cols);
      expect(c.tile[1]).toBeLessThan(world.rows);
    }
  });

  it('t1 无门槛 / t2 需钥匙 / t3 需工具', () => {
    const needs = Object.fromEntries(chests.map((c) => [c.tier, c.needs]));
    expect(needs.t1).toBeNull();
    expect(needs.t2).toBe('key');
    expect(needs.t3).toBe('tool');
  });

  it('档位越高金币越高', () => {
    const tiers = fixture.chest_tiers as Record<string, { coins: number[] }>;
    expect(tiers.t1.coins[1]).toBeLessThan(tiers.t2.coins[0]);
    expect(tiers.t2.coins[1]).toBeLessThan(tiers.t3.coins[0]);
  });

  it('visibleChests 只返回已发现的（未发现的不许预画）', () => {
    const first = chests[0];
    expect(visibleChests(chests, [first.id])).toEqual([first]);
    expect(visibleChests(chests, [])).toEqual([]);
  });

  it('chestValueLine 标出门槛', () => {
    const t3 = chests.find((c) => c.tier === 't3')!;
    expect(chestValueLine(t3)).toContain('需工具');
    const t1 = chests.find((c) => c.tier === 't1')!;
    expect(chestValueLine(t1)).not.toContain('需');
  });
});

describe('秘密区域', () => {
  it('至少 6 个区域', () => {
    expect(secrets.length).toBeGreaterThanOrEqual(6);
  });

  it('id 唯一', () => {
    expect(new Set(secrets.map((s) => s.id)).size).toBe(secrets.length);
  });

  it('nearbySecrets 只给附近的', () => {
    const near = nearbySecrets(secrets, secrets[0].tile);
    expect(near).toContain(secrets[0]);
    expect(nearbySecrets(secrets, [world.cols - 1, 0])).toEqual([]);
  });
});

describe('随机事件契约', () => {
  it('后端确实有诚实态（空手）事件', () => {
    const empty = fixture.empty_event_ids as string[];
    expect(empty.length).toBeGreaterThanOrEqual(2);
  });

  it('空手率不是 0（不能每次都有收获）', () => {
    expect(fixture.empty_pct as number).toBeGreaterThanOrEqual(15);
  });

  it('每条文案都没有残留模板占位符', () => {
    for (const e of events) {
      expect(e.line).not.toContain('{');
      expect(e.label).toBeTruthy();
    }
  });

  it('empty 事件确实不给东西', () => {
    const emptyIds = new Set(fixture.empty_event_ids as string[]);
    for (const e of events) {
      if (emptyIds.has(e.event_id)) {
        expect(e.materials).toHaveLength(0);
        expect(e.coins).toBe(0);
      }
    }
  });

  it('eventLine 如实显示空手而不是编收获', () => {
    const emptyEvent: EventOutcome = {
      event_id: 'nothing',
      label: '一无所获',
      line: '这里什么也没有。',
      materials: [],
      coins: 0,
      empty: true,
    };
    expect(eventLine(emptyEvent)).toBe('一无所获：这里什么也没有。');
  });

  it('eventLine 把产出列进括号', () => {
    const e = events.find((x) => x.materials.length > 0 || x.coins > 0);
    if (e) expect(eventLine(e)).toContain('（');
  });
});

describe('展示派生', () => {
  it('mapSizeLine 含格数与像素数', () => {
    expect(mapSizeLine(world)).toBe('160 × 100 格 · 5120 × 3200');
  });

  it('terrainShareLines 占比降序且带百分号', () => {
    const lines = terrainShareLines(world.histogram, fixture.terrain_labels as Record<string, string>);
    expect(lines.length).toBe(4);
    expect(lines[0]).toMatch(/%$/);
  });

  it('空直方图不编数据', () => {
    expect(terrainShareLines({})).toEqual([]);
  });

  it('gradeOf 与后端阈值一致', () => {
    expect(gradeOf(0.0)[0]).toBe('初来乍到');
    expect(gradeOf(0.3)[0]).toBe('熟门熟路');
    expect(gradeOf(0.6)[0]).toBe('四处走走');
    expect(gradeOf(0.9)[0]).toBe('无所不知');
  });

  it('gradeOf 对负数/NaN 走兜底而不是 NaN 文案', () => {
    expect(gradeOf(-1, ['未知', '没数据'])[0]).toBe('未知');
    expect(gradeOf(Number.NaN, ['未知', '没数据'])[0]).toBe('未知');
  });

  it('fixture 的 grade 段落在已知评语里', () => {
    const [name] = fixture.grade as [string, string];
    expect(['初来乍到', '转了转', '熟门熟路', '四处走走', '无所不知']).toContain(name);
  });
});

describe('探索度与可达性数据诚实', () => {
  it('可达格数不超过可走格数', () => {
    expect(world.reachable_from_spawn).toBeLessThanOrEqual(
      world.cols * world.rows * world.walkable_ratio + 1,
    );
  });

  it('可达比例接近 1（没有孤岛死区）', () => {
    expect(world.reachable_from_spawn).toBeGreaterThan(
      world.cols * world.rows * world.walkable_ratio * 0.99,
    );
  });
});
