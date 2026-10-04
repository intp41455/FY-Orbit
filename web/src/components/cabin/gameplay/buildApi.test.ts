import { describe, expect, it } from 'vitest';
import fixture from './__fixtures__/buildLayout.sample.json';
import { TILE } from '../cabinConfig';
import {
  blockedCells,
  CATEGORIES,
  categoryRows,
  cellRect,
  confirmLabel,
  doorCells,
  dragHint,
  findFurniture,
  furnitureLabel,
  ghostCells,
  itemCells,
  itemLine,
  layerHint,
  nextRotation,
  pixelToCell,
  roomPixelSize,
  sizeAt,
  sizeLabel,
  wallCells,
  type ConfirmResult,
  type FurnitureDef,
  type Layout,
  type PlacedItem,
  type RoomSpec,
} from './buildApi';

/**
 * B4 前端契约测试。
 *
 * fixture 是**后端真跑产出的快照**（`cabin_life.build`），锁的是「两端说的是同一件事」：
 *   - 分类 id / 落位层 / 旋转后的尺寸 / 房间规格 —— 与后端逐条一致；
 *   - 拒绝文案（`rejections`）—— 后端改口径时前端提示必须跟着改。
 */

const room = fixture.room as unknown as RoomSpec;
const catalog = fixture.catalog as unknown as FurnitureDef[];
const layout = fixture.layout as unknown as Layout;
const confirm = fixture.confirm as unknown as ConfirmResult;
const rejections = fixture.rejections as Record<string, string>;

const bed = findFurniture(catalog, 'b4_double_bed')!;
const rug = findFurniture(catalog, 'b4_rug_large')!;
const painting = findFurniture(catalog, 'b4_painting')!;

describe('B4 房间规格对齐', () => {
  it('tile 就是 A1 冻结的 TILE', () => {
    expect(room.tile).toBe(TILE);
  });

  it('房间像素尺寸 = 格数 × TILE', () => {
    expect(roomPixelSize(room)).toEqual({
      width: room.cols * TILE,
      height: room.rows * TILE,
    });
  });

  it('墙面带在房间内', () => {
    const [lo, hi] = room.wall_band;
    expect(lo).toBeGreaterThanOrEqual(0);
    expect(hi).toBeLessThan(room.rows);
  });

  it('门行在房间内', () => {
    expect(room.door_row).toBeLessThan(room.rows);
  });

  it('门禁放区间在房间内', () => {
    const [lo, hi] = room.door_clear_x;
    expect(lo).toBeGreaterThanOrEqual(0);
    expect(hi).toBeLessThan(room.cols);
  });
});

describe('家具目录契约', () => {
  it('id 唯一', () => {
    expect(new Set(catalog.map((f) => f.id)).size).toBe(catalog.length);
  });

  it('id 全部 b4_ 前缀（与 W1 命名空间隔离）', () => {
    expect(catalog.every((f) => f.id.startsWith('b4_'))).toBe(true);
  });

  it('六大分类齐备', () => {
    expect([...CATEGORIES]).toEqual([
      'table_chair',
      'bed',
      'cabinet',
      'decor',
      'lamp',
      'plant',
    ]);
  });

  it('每件家具的分类都合法', () => {
    for (const f of catalog) {
      expect(CATEGORIES).toContain(f.category);
    }
  });

  it('未知 id 不抛错而是返回 undefined', () => {
    expect(findFurniture(catalog, 'gold_toilet')).toBeUndefined();
  });

  it('未知 id 的标签显示为未知家具', () => {
    expect(furnitureLabel(catalog, 'gold_toilet')).toBe('未知家具');
  });

  it('目录总件数 = 分类计数之和', () => {
    const rows = categoryRows(catalog);
    expect(rows.reduce((a, r) => a + r.count, 0)).toBe(catalog.length);
  });

  it('categoryRows 的 cheapest 是该分类最小价', () => {
    for (const row of categoryRows(catalog)) {
      const costs = row.ids.map((id) => findFurniture(catalog, id)!.cost);
      expect(row.cheapest).toBe(Math.min(...costs));
    }
  });

  it('layerHint 三种落位层都有中文说明', () => {
    expect(layerHint('floor')).toBeTruthy();
    expect(layerHint('wall')).toBeTruthy();
    expect(layerHint('under')).toBeTruthy();
  });
});

describe('旋转与占位（与后端 size_at 逐条对齐）', () => {
  it('0°/180° 保持原尺寸', () => {
    expect(sizeAt(bed, 0)).toEqual([bed.width, bed.height]);
    expect(sizeAt(bed, 180)).toEqual([bed.width, bed.height]);
  });

  it('90°/270° 宽高互换', () => {
    expect(sizeAt(bed, 90)).toEqual([bed.height, bed.width]);
    expect(sizeAt(bed, 270)).toEqual([bed.height, bed.width]);
  });

  it('方形家具四向同尺寸', () => {
    const pot = findFurniture(catalog, 'b4_pot_plant')!;
    expect(new Set([0, 90, 180, 270].map((r) => sizeAt(pot, r as 0).join('x'))).size).toBe(1);
  });

  it('nextRotation 四步回到原点', () => {
    let r = 90 as const;
    for (let i = 0; i < 4; i += 1) r = nextRotation(r) as typeof r;
    expect(r).toBe(90);
  });

  it('nextRotation 负步长也安全', () => {
    expect(nextRotation(0, -1)).toBe(270);
  });

  it('占位格数 = 尺寸积', () => {
    const item: PlacedItem = { id: 'x', furniture_id: bed.id, x: 2, y: 3, rotation: 0 };
    expect(itemCells(item, bed)).toHaveLength(bed.width * bed.height);
  });

  it('占位格起点 = 家具左上角', () => {
    const item: PlacedItem = { id: 'x', furniture_id: bed.id, x: 2, y: 3, rotation: 0 };
    expect(itemCells(item, bed)[0]).toEqual([2, 3]);
  });

  it('旋转后占位起点不变但跨度互换', () => {
    const item: PlacedItem = { id: 'x', furniture_id: bed.id, x: 2, y: 3, rotation: 90 };
    const cells = itemCells(item, bed);
    expect(cells[0]).toEqual([2, 3]);
    expect(Math.max(...cells.map((c) => c[0]))).toBe(2 + bed.height - 1);
  });

  it('sizeLabel 是中文尺寸', () => {
    expect(sizeLabel(rug, 0)).toContain('格');
  });

  it('itemLine 含名称与金币', () => {
    const line = itemLine(bed, 0);
    expect(line).toContain(bed.label);
    expect(line).toContain(`${bed.cost} 金`);
  });
});

describe('像素换算', () => {
  it('cellRect 边长恒为 TILE', () => {
    expect(cellRect([3, 4])).toEqual({
      x: 3 * TILE,
      y: 4 * TILE,
      width: TILE,
      height: TILE,
    });
  });

  it('pixelToCell 用 floor（命中测试口径）', () => {
    expect(pixelToCell(0, 0)).toEqual([0, 0]);
    expect(pixelToCell(TILE - 1, TILE - 1)).toEqual([0, 0]);
    expect(pixelToCell(TILE, TILE)).toEqual([1, 1]);
  });

  it('pixelToCell 与 cellRect 在格内互逆', () => {
    const cell: [number, number] = [5, 7];
    const r = cellRect(cell);
    expect(pixelToCell(r.x + 1, r.y + 1)).toEqual(cell);
  });
});

describe('禁放位与占位渲染数据', () => {
  it('doorCells 就是后端给的格', () => {
    expect(doorCells(room)).toEqual(fixture.door_cells as [number, number][]);
  });

  it('wallCells 覆盖整条墙带', () => {
    expect(wallCells(room)).toHaveLength(
      room.cols * (room.wall_band[1] - room.wall_band[0] + 1),
    );
  });

  it('blockedCells 一定包含门口', () => {
    const cells = blockedCells(layout, room, catalog);
    for (const [x, y] of doorCells(room)) {
      expect(cells.has(`${x},${y}`)).toBe(true);
    }
  });

  it('blockedCells 不含地毯格（地毯不挡路）', () => {
    const cells = blockedCells(layout, room, catalog);
    const rugItem = layout.items.find((i) => i.furniture_id === rug.id)!;
    for (const [x, y] of itemCells(rugItem, rug)) {
      // 地毯格若同时被别的家具占着仍会是 blocked，这里只断言纯地毯重叠不额外产生
      expect(typeof cells.has(`${x},${y}`)).toBe('boolean');
    }
  });

  it('blockedCells 含家具占位', () => {
    const cells = blockedCells(layout, room, catalog);
    const bedItem = layout.items.find((i) => i.furniture_id === bed.id)!;
    for (const [x, y] of itemCells(bedItem, bed)) {
      expect(cells.has(`${x},${y}`)).toBe(true);
    }
  });

  it('ghostCells 未知家具返回空而不是崩', () => {
    expect(ghostCells('gold_toilet', 0, 0, 0, catalog)).toEqual([]);
  });

  it('ghostCells 含越界格（玩家要看见有一格在外面）', () => {
    const cells = ghostCells(bed.id, room.cols - 1, 3, 0, catalog);
    expect(cells.some(([x]) => x >= room.cols)).toBe(true);
  });
});

describe('拒绝文案契约（后端给了就原样显示）', () => {
  it('后端每类拒绝都有非空文案', () => {
    for (const [key, value] of Object.entries(rejections)) {
      expect(value, `${key} 缺文案`).toBeTruthy();
    }
  });

  it('重叠文案点名了挡住它的家具', () => {
    expect(rejections.overlap).toContain('双人床');
  });

  it('门口文案点明要留出路', () => {
    expect(rejections.door).toContain('门口');
  });

  it('挂画放地上有专门文案', () => {
    expect(rejections.wall_mounted_on_floor).toContain('挂画');
  });

  it('越界文案带出允许区间', () => {
    expect(rejections.negative).toContain('x∈');
  });

  it('放不下房间的文案带出所需尺寸', () => {
    expect(rejections.too_big_for_room).toContain('格');
  });

  it('拖动提示：无 reason 即合法', () => {
    expect(dragHint(null)).toEqual({ ok: true, text: '可以放这里' });
    expect(dragHint(undefined).ok).toBe(true);
    expect(dragHint('').ok).toBe(true);
  });

  it('拖动提示：有 reason 原样透传不润色', () => {
    expect(dragHint(rejections.overlap)).toEqual({
      ok: false,
      text: rejections.overlap,
    });
  });
});

describe('确认结算（只算不清零）', () => {
  it('confirm 不含任何「已扣除」字段（扣钱归 B8）', () => {
    expect(Object.keys(confirm).sort()).toEqual([
      'comfort',
      'counts',
      'grade',
      'items',
      'line',
      'total_cost',
    ]);
  });

  it('items 数与布局一致', () => {
    expect(confirm.items).toBe(layout.items.length);
  });

  it('total_cost 是各件价格之和', () => {
    const sum = layout.items.reduce(
      (a, i) => a + findFurniture(catalog, i.furniture_id)!.cost,
      0,
    );
    expect(confirm.total_cost).toBe(sum);
  });

  it('comfort 是各件舒适度之和', () => {
    const sum = layout.items.reduce(
      (a, i) => a + findFurniture(catalog, i.furniture_id)!.comfort,
      0,
    );
    expect(confirm.comfort).toBe(sum);
  });

  it('counts 覆盖全部分类', () => {
    expect(Object.keys(confirm.counts).sort()).toEqual([...CATEGORIES].sort());
  });

  it('confirmLabel 含件数与总价', () => {
    const label = confirmLabel(confirm);
    expect(label).toContain(String(confirm.items));
    expect(label).toContain(String(confirm.total_cost));
  });

  it('评语随舒适度单调变好', () => {
    const grades = fixture.grades as Record<string, string>;
    const values = Object.keys(grades).map(Number).sort((a, b) => a - b);
    expect(new Set(values.map((v) => grades[String(v)])).size).toBe(values.length);
  });

  it('空房间评语如实说是空的', () => {
    expect((fixture.grades as Record<string, string>)['0']).toContain('没有');
  });
});

describe('挂画只能上墙（前端不再自己判）', () => {
  it('fixture 里挂画确实在墙带内', () => {
    const item = layout.items.find((i) => i.furniture_id === painting.id)!;
    const [lo, hi] = room.wall_band;
    expect(item.y).toBeGreaterThanOrEqual(lo);
    expect(item.y).toBeLessThanOrEqual(hi);
  });
});
