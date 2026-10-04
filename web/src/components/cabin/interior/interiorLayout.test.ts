import { beforeEach, describe, expect, it } from 'vitest';
import {
  addItem,
  clampToRoom,
  cycleColorway,
  defaultLayout,
  depthKey,
  deriveCabinLevel,
  flipItem,
  interiorStorageKey,
  isLayoutTooLarge,
  loadLocalLayout,
  MAX_ITEMS,
  moveItem,
  removeItem,
  resetItemIds,
  rollDiceFurniture,
  sanitizeLayout,
  saveLocalLayout,
  shiftLayer,
  snapToGrid,
  sortByDepth,
  GRID,
  ROOM_COLS,
  type InteriorItem,
  type InteriorLayout,
} from './interiorLayout';
import { FURNITURE_CATALOG, FURNITURE_IDS, unlockedFurniture } from './furnitureCatalog';
import { getColorwayCount } from './furnitureArt';

beforeEach(() => {
  resetItemIds();
  localStorage.clear();
});

const item = (over: Partial<InteriorItem> = {}): InteriorItem => ({
  id: 'i1',
  furnitureId: 'chair',
  x: 3,
  y: 4,
  flipped: false,
  colorway: 0,
  z: 0,
  ...over,
});

describe('W1 布局：坐标与吸附', () => {
  it('吸附到 16px 网格（负值向下取整，保证单调）', () => {
    expect(snapToGrid(0)).toBe(0);
    expect(snapToGrid(15)).toBe(0);
    expect(snapToGrid(16)).toBe(1);
    expect(snapToGrid(-1)).toBe(-1);
    expect(snapToGrid(-16)).toBe(-1);
  });

  it('地板家具按房间边界夹取，不会飞出房间', () => {
    // 3 格宽的床放到最右 → x 被夹到 ROOM_COLS - 3
    expect(clampToRoom('bed', 999, 3)).toEqual({ x: ROOM_COLS - 3, y: 3 });
    expect(clampToRoom('bed', -50, -50)).toEqual({ x: 0, y: 0 });
  });

  it('墙面家具夹在墙面带、天花板家具夹在天花板带（mount 决定可用区域）', () => {
    const pic = clampToRoom('picture_frame', 5, 99);
    expect(pic.y).toBeLessThanOrEqual(3); // wall 带 0..3
    const lamp = clampToRoom('ceiling_lamp', 5, 99);
    expect(lamp.y).toBeLessThanOrEqual(1); // ceiling 带 0..1
  });

  it('未知家具 id 不报错，夹到原点（诚实失败而非抛异常）', () => {
    expect(clampToRoom('not_a_chair', 5, 5)).toEqual({ x: 0, y: 0 });
  });
});

describe('W1 布局：伪深度排序（y 越大越靠前）', () => {
  it('depthKey 随 y + 高度单调递增', () => {
    const a = item({ id: 'a', y: 1, z: 0 });
    const b = item({ id: 'b', y: 6, z: 0 });
    expect(depthKey(b)).toBeGreaterThan(depthKey(a));
  });

  it('同 y 时用 z 决定前后', () => {
    const back = item({ id: 'back', y: 4, z: 0 });
    const front = item({ id: 'front', y: 4, z: 5 });
    expect(depthKey(front)).toBeGreaterThan(depthKey(back));
  });

  it('sortByDepth 稳定且不改动入参（原数组顺序不变）', () => {
    const input = [
      item({ id: 'c', y: 8, z: 0 }),
      item({ id: 'a', y: 1, z: 0 }),
      item({ id: 'b', y: 4, z: 0 }),
    ];
    const snapshot = input.map((i) => i.id);
    const sorted = sortByDepth(input).map((i) => i.id);
    expect(sorted).toEqual(['a', 'b', 'c']);
    expect(input.map((i) => i.id)).toEqual(snapshot);
  });
});

describe('W1 布局：编辑操作', () => {
  it('addItem 追加并递增版本；重复加到上限后原样返回（不静默丢弃）', () => {
    let layout = defaultLayout('cabin');
    const before = layout.items.length;
    layout = addItem(layout, 'plant', 3, 3);
    expect(layout.items.length).toBe(before + 1);

    let full = layout;
    for (let i = full.items.length; i < MAX_ITEMS; i += 1) {
      full = addItem(full, 'plant', 2, 2);
    }
    expect(full.items.length).toBe(MAX_ITEMS);
    const overflow = addItem(full, 'plant', 2, 2);
    expect(overflow).toBe(full); // 引用相同 = 未改动
  });

  it('addItem 拒绝未注册家具 id（原样返回，不塞脏数据）', () => {
    const layout = defaultLayout('cave');
    expect(addItem(layout, 'unicorn_rug', 1, 1)).toBe(layout);
  });

  it('moveItem 移动并夹取到房间内；未知 id 不新增也不报错', () => {
    const layout = defaultLayout('cabin');
    const target = layout.items[0]!;
    const moved = moveItem(layout, target.id, 999, 999);
    const found = moved.items.find((i) => i.id === target.id)!;
    expect(found.x).toBeLessThanOrEqual(ROOM_COLS - 1);
    expect(moved.items.length).toBe(layout.items.length);
  });

  it('removeItem / flipItem / cycleColorway / shiftLayer 各司其职', () => {
    let layout = defaultLayout('cabin');
    const id = layout.items[0]!.id;

    const removed = removeItem(layout, id);
    expect(removed.items.length).toBe(layout.items.length - 1);
    expect(removeItem(removed, 'nope')).toBe(removed); // 无变化 → 原引用

    const flipped = flipItem(layout, id);
    expect(flipped.items.find((i) => i.id === id)!.flipped).toBe(true);

    const maxCw = getColorwayCount('bed');
    const recolored = cycleColorway(layout, 'bed-does-not-exist');
    expect(recolored.items.length).toBe(layout.items.length);

    const bedId = layout.items.find((i) => i.furnitureId === 'bed')!.id;
    const cw0 = cycleColorway(layout, bedId).items.find((i) => i.id === bedId)!.colorway;
    expect(cw0).toBe(1 % maxCw);

    const z0 = layout.items.find((i) => i.id === id)!.z;
    const layered = shiftLayer(layout, id, 2);
    expect(layered.items.find((i) => i.id === id)!.z).toBe(z0 + 2);
  });
});

describe('W1 布局：小屋等级（≥4 件家具由等级解锁）', () => {
  it('家具越多等级越高，封顶 Lv5', () => {
    expect(deriveCabinLevel(0)).toBe(1);
    expect(deriveCabinLevel(3)).toBe(2);
    expect(deriveCabinLevel(7)).toBe(3);
    expect(deriveCabinLevel(11)).toBe(4);
    expect(deriveCabinLevel(16)).toBe(5);
    expect(deriveCabinLevel(999)).toBe(5);
  });

  it('默认布置至少解锁 1 件等级家具（Lv2 起）', () => {
    const layout = defaultLayout('castle');
    const level = deriveCabinLevel(layout.items.length);
    const ids = new Set(unlockedFurniture({ level, theme: 'castle' }).map((f) => f.id));
    const levelUnlocked = FURNITURE_CATALOG.filter((f) => f.unlockedBy === 'level' && ids.has(f.id));
    expect(levelUnlocked.length).toBeGreaterThanOrEqual(1);
  });

  it('quest / craft 家具在未接入时一律未解锁（不假装可用）', () => {
    const gated = FURNITURE_CATALOG.filter((f) => f.unlockedBy === 'quest' || f.unlockedBy === 'craft');
    expect(gated.length).toBeGreaterThan(0);
    // 即使等级拉满 + 主题匹配，quest 家具依然锁着（W2 任务线未接入）
    for (const theme of ['planet', 'snowcave', 'garden']) {
      const ids = new Set(unlockedFurniture({ level: 5, theme }).map((f) => f.id));
      for (const f of gated) {
        if (f.unlockedBy === 'quest') expect(ids.has(f.id)).toBe(false);
      }
    }
    // craft 家具在未传 craftedIds 时锁着
    const idsNoCraft = new Set(unlockedFurniture({ level: 5, theme: 'garden' }).map((f) => f.id));
    expect(idsNoCraft.has('herb_shelf')).toBe(false);
  });
});

describe('W1 布局：默认布置按房屋模板隔离', () => {
  it('每个模板都含更衣镜（角色工坊入口常驻）', () => {
    for (const house of ['villa', 'cabin', 'cave', 'snowcave', 'bunker', 'castle']) {
      const layout = defaultLayout(house);
      expect(layout.items.some((i) => i.furnitureId === 'mirror')).toBe(true);
    }
  });

  it('不同模板返回不同布置，且 houseId 与请求一致', () => {
    const cabin = defaultLayout('cabin');
    const castle = defaultLayout('castle');
    expect(cabin.houseId).toBe('cabin');
    expect(castle.houseId).toBe('castle');
    expect(cabin.items.map((i) => i.furnitureId)).not.toEqual(castle.items.map((i) => i.furnitureId));
  });

  it('默认布置的坐标都已被夹取到房间内（无需二次修正）', () => {
    for (const house of ['villa', 'cabin', 'cave', 'snowcave', 'bunker', 'castle']) {
      for (const it of defaultLayout(house).items) {
        const def = FURNITURE_CATALOG.find((f) => f.id === it.furnitureId)!;
        expect(it.x).toBeGreaterThanOrEqual(0);
        expect(it.y).toBeGreaterThanOrEqual(0);
        expect(it.x).toBeLessThanOrEqual(ROOM_COLS - def.sizeCells.w);
      }
    }
  });
});

describe('W1 布局：sanitize 白名单清洗', () => {
  it('丢弃未注册家具 id，但保留合法项（尽可能救回而不是整份报废）', () => {
    const clean = sanitizeLayout(
      {
        version: 3,
        items: [
          { id: 'ok', furnitureId: 'bed', x: 2, y: 2, colorway: 0, z: 0 },
          { id: 'bad', furnitureId: 'dragon_wardrobe', x: 1, y: 1 },
          null,
          'nonsense',
        ],
      },
      'cabin',
    );
    expect(clean.items.length).toBe(1);
    expect(clean.items[0]!.furnitureId).toBe('bed');
    expect(clean.version).toBe(3);
  });

  it('坐标越界被夹取，colorway 取整，flipped 转布尔', () => {
    const clean = sanitizeLayout(
      {
        items: [{ id: 'a', furnitureId: 'chair', x: 9999, y: -50, colorway: 2.7, flipped: 1, z: 0.4 }],
      },
      'cabin',
    );
    const it = clean.items[0]!;
    expect(it.x).toBeLessThanOrEqual(ROOM_COLS - 1);
    expect(it.y).toBeGreaterThanOrEqual(0);
    expect(it.colorway).toBe(3);
    expect(it.flipped).toBe(true);
  });

  it('重复 id 自动去重，不会产生渲染歧义', () => {
    const clean = sanitizeLayout(
      {
        items: [
          { id: 'same', furnitureId: 'chair', x: 1, y: 1 },
          { id: 'same', furnitureId: 'chair', x: 2, y: 2 },
        ],
      },
      'cabin',
    );
    const ids = clean.items.map((i) => i.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('完全非法的载荷回退到该模板的默认布置', () => {
    expect(sanitizeLayout(null, 'castle').items.length).toBe(defaultLayout('castle').items.length);
    expect(sanitizeLayout({ items: 'nope' }, 'cabin').items.length).toBeGreaterThan(0);
  });

  it('item 数被截断到 MAX_ITEMS', () => {
    const many = Array.from({ length: MAX_ITEMS + 40 }, (_, i) => ({
      id: `x${i}`,
      furnitureId: 'chair',
      x: 1,
      y: 1,
    }));
    expect(sanitizeLayout({ items: many }, 'cabin').items.length).toBe(MAX_ITEMS);
  });
});

describe('W1 布局：骰子（Summerhouse 灵感投放）', () => {
  it('rng 固定 → 结果确定可测', () => {
    const base = defaultLayout('cabin');
    const a = rollDiceFurniture(base, { level: 1, rng: () => 0, count: 3 });
    const b = rollDiceFurniture(base, { level: 1, rng: () => 0, count: 3 });
    expect(a.items.map((i) => i.furnitureId)).toEqual(b.items.map((i) => i.furnitureId));
  });

  it('只投已解锁家具，绝不凭空造出 quest/craft 家具', () => {
    const base = defaultLayout('cave');
    const rolled = rollDiceFurniture(base, { level: 5, rng: () => 0.5, count: 3 });
    const allowed = new Set(unlockedFurniture({ level: 5, theme: 'cave' }).map((f) => f.id));
    for (const it of rolled.items) {
      expect(FURNITURE_IDS).toContain(it.furnitureId);
      expect(allowed.has(it.furnitureId)).toBe(true);
    }
  });

  it('已满时原样返回（不越界）', () => {
    let l: InteriorLayout = defaultLayout('cabin');
    for (let i = l.items.length; i < MAX_ITEMS; i += 1) l = addItem(l, 'chair', 1, 1);
    expect(rollDiceFurniture(l, { level: 5, rng: () => 0.3 })).toBe(l);
  });
});

describe('W1 布局：localStorage 双写', () => {
  it('按房屋模板分套存取 key 互不覆盖', () => {
    expect(interiorStorageKey('cabin')).not.toBe(interiorStorageKey('castle'));
    const cabin = defaultLayout('cabin');
    const castle = defaultLayout('castle');
    expect(saveLocalLayout(cabin)).toBe(true);
    expect(saveLocalLayout(castle)).toBe(true);
    expect(loadLocalLayout('cabin')!.houseId).toBe('cabin');
    expect(loadLocalLayout('castle')!.houseId).toBe('castle');
  });

  it('保存 → 读取往返一致', () => {
    const layout = addItem(defaultLayout('villa'), 'mirror', 4, 4);
    saveLocalLayout(layout);
    const back = loadLocalLayout('villa')!;
    expect(back.items.length).toBe(layout.items.length);
    expect(back.items.map((i) => i.furnitureId)).toEqual(layout.items.map((i) => i.furnitureId));
  });

  it('损坏 JSON / 无存档 → 返回 null，不抛错（诚实失败）', () => {
    expect(loadLocalLayout('nothing-here')).toBeNull();
    localStorage.setItem(interiorStorageKey('bunker'), '{broken');
    expect(loadLocalLayout('bunker')).toBeNull();
  });

  it('存储不可用（隐私模式）→ saveLocalLayout 返回 false 而不是假装成功', () => {
    const ok = saveLocalLayout(defaultLayout('cave'), {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceeded');
      },
    } as unknown as Storage);
    expect(ok).toBe(false);
    expect(loadLocalLayout('cave', null)).toBeNull();
  });
});

describe('W1 布局：体积护栏', () => {
  it('正常布置远小于 64KB 上限', () => {
    expect(isLayoutTooLarge(defaultLayout('castle'))).toBe(false);
  });

  it('超限布局被判定为过大（与后端同规则）', () => {
    const fat = {
      houseId: 'cabin',
      version: 1,
      items: Array.from({ length: MAX_ITEMS }, (_, i) => ({
        id: `i${i}`,
        furnitureId: 'bookshelf',
        x: 1,
        y: 1,
        flipped: false,
        colorway: 0,
        z: 0,
        // 额外大字段撑爆体积
        junk: 'x'.repeat(1200),
      })),
    };
    expect(isLayoutTooLarge(fat as never)).toBe(true);
  });
});

describe('W1 布局：网格常量与任务书一致', () => {
  it('GRID = 16px（任务书硬要求）', () => {
    expect(GRID).toBe(16);
  });
});
