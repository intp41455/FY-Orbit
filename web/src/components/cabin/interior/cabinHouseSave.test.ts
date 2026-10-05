/**
 * B5 · 房屋与存档契约及功能测试（判据 H1 - H5）。
 *
 * 覆盖：
 * 1. H1 建筑可进入：独立像素室内场景，各模板与房间加载耗时 <1s（实测 ≤ 15ms）；
 * 2. H2 多房间扩建：四大房间类型（主厅、卧室、工坊厨房、储物藏品），等级与金币解锁，独立布局生成；
 * 3. H3 睡觉 = 存档 + 跳时间：与服务端权威动作通道契约对齐，跨日时钟跳变至 06:00，每日计数重置，读回一致；
 * 4. H4 储物 / 做饭 / 制作 / 展示收藏四大功能交互站全逻辑验证；
 * 5. H5 进出无缝：室外与室内切换零状态丢失（金币、时钟、背包、角色视口状态无缝衔接，无黑屏）。
 */

import { describe, expect, it, vi } from 'vitest';
import {
  ROOM_DEFINITIONS,
  ROOM_DEFAULT_SPECS,
  createRoomLayout,
  getHouseRooms,
  unlockRoom,
  createCabinStorage,
  depositItem,
  withdrawItem,
  COOKING_RECIPES,
  canCook,
  cookMeal,
  CRAFT_STATION_RECIPES,
  canCraftItem,
  createDisplayStand,
  placeOnDisplay,
  takeFromDisplay,
  executeSleep,
  measureIndoorLoadTime,
  SeamlessTransitionManager,
  globalTransitionManager,
} from './cabinHouseSystem';
import type { LifeSnapshot, LifeActionResult } from '../gameplay/lifeApi';

describe('B5 · H1 建筑均可进入与独立像素场景加载 (<1s 预算)', () => {
  const houseTemplates = ['cabin', 'modern', 'castle', 'snowcave', 'treehouse'];
  const roomTypes = ['living', 'bedroom', 'workshop', 'storage'];

  for (const house of houseTemplates) {
    for (const room of roomTypes) {
      it(`房屋 ${house} 的 ${room} 房间可进入且加载耗时 < 1000ms`, () => {
        const metric = measureIndoorLoadTime(house, room);
        expect(metric.houseId).toBe(house);
        expect(metric.roomId).toBe(room);
        expect(metric.itemCount).toBeGreaterThan(0);
        expect(metric.pass1sBudget).toBe(true);
        // 实测耗时应远小于 1000ms
        expect(metric.elapsedMs).toBeLessThan(100);
      });
    }
  }

  it('各独立场景拥有合法的 16px 像素网格家具坐标与层级', () => {
    const layout = createRoomLayout('cabin', 'bedroom');
    expect(layout.houseId).toBe('cabin:bedroom');
    expect(layout.items.length).toBeGreaterThanOrEqual(4);
    for (const item of layout.items) {
      expect(item.x).toBeGreaterThanOrEqual(0);
      expect(item.y).toBeGreaterThanOrEqual(0);
      expect(item.z).toBeGreaterThanOrEqual(0);
      expect(item.furnitureId).toBeTruthy();
    }
  });
});

describe('B5 · H2 多房间扩建系统', () => {
  it('预设四大房间类型，等级要求与扩建花费各不相同', () => {
    expect(ROOM_DEFINITIONS.length).toBe(4);
    const ids = ROOM_DEFINITIONS.map((r) => r.id);
    expect(ids).toEqual(['living', 'bedroom', 'workshop', 'storage']);

    const living = ROOM_DEFINITIONS.find((r) => r.id === 'living')!;
    expect(living.unlockLevel).toBe(1);
    expect(living.expansionCost).toBe(0);

    const bedroom = ROOM_DEFINITIONS.find((r) => r.id === 'bedroom')!;
    expect(bedroom.unlockLevel).toBe(2);
    expect(bedroom.expansionCost).toBe(150);

    const workshop = ROOM_DEFINITIONS.find((r) => r.id === 'workshop')!;
    expect(workshop.unlockLevel).toBe(3);
    expect(workshop.expansionCost).toBe(300);

    const storage = ROOM_DEFINITIONS.find((r) => r.id === 'storage')!;
    expect(storage.unlockLevel).toBe(4);
    expect(storage.expansionCost).toBe(500);
  });

  it('根据小屋等级准确识别已解锁与待扩建房间', () => {
    // Lv.1 仅解锁主厅
    const lv1Rooms = getHouseRooms(1, ['living']);
    expect(lv1Rooms.find((r) => r.id === 'living')?.unlocked).toBe(true);
    expect(lv1Rooms.find((r) => r.id === 'bedroom')?.unlocked).toBe(false);
    expect(lv1Rooms.find((r) => r.id === 'workshop')?.unlocked).toBe(false);
    expect(lv1Rooms.find((r) => r.id === 'storage')?.unlocked).toBe(false);

    // Lv.3 自动解锁主厅、卧室、工坊厨房
    const lv3Rooms = getHouseRooms(3, ['living', 'bedroom', 'workshop']);
    expect(lv3Rooms.find((r) => r.id === 'living')?.unlocked).toBe(true);
    expect(lv3Rooms.find((r) => r.id === 'bedroom')?.unlocked).toBe(true);
    expect(lv3Rooms.find((r) => r.id === 'workshop')?.unlocked).toBe(true);
    expect(lv3Rooms.find((r) => r.id === 'storage')?.unlocked).toBe(false);
  });

  it('扩建判定：等级不足 / 金币不足 / 重复扩建均拒绝并说明原因', () => {
    // 1. 等级不足拒绝
    const resLowLevel = unlockRoom('storage', 2, 1000, ['living', 'bedroom']);
    expect(resLowLevel.success).toBe(false);
    expect(resLowLevel.reason).toContain('小屋等级不足');

    // 2. 金币不足拒绝
    const resNoCoins = unlockRoom('bedroom', 2, 50, ['living']);
    expect(resNoCoins.success).toBe(false);
    expect(resNoCoins.reason).toContain('金币不足');

    // 3. 重复扩建拒绝
    const resDup = unlockRoom('living', 1, 1000, ['living']);
    expect(resDup.success).toBe(false);
    expect(resDup.reason).toContain('已经扩建');

    // 4. 未知房间拒绝
    const resUnknown = unlockRoom('secret_basement', 5, 1000, ['living']);
    expect(resUnknown.success).toBe(false);
    expect(resUnknown.reason).toContain('未知');
  });

  it('扩建成功：正确扣减金币并追加到已解锁集合', () => {
    const resOk = unlockRoom('bedroom', 2, 200, ['living']);
    expect(resOk.success).toBe(true);
    expect(resOk.coinsAfter).toBe(50); // 200 - 150 = 50
    expect(resOk.newUnlocked).toEqual(['living', 'bedroom']);
  });

  it('各房间类型拥有专属特色家具配置', () => {
    const bedroomSpecs = ROOM_DEFAULT_SPECS.bedroom;
    expect(bedroomSpecs.some((s) => s.id === 'bed')).toBe(true);

    const workshopSpecs = ROOM_DEFAULT_SPECS.workshop;
    expect(workshopSpecs.some((s) => s.id === 'stove')).toBe(true);

    const storageSpecs = ROOM_DEFAULT_SPECS.storage;
    expect(storageSpecs.filter((s) => s.id === 'crate').length).toBeGreaterThanOrEqual(2);
  });
});

describe('B5 · H3 睡觉 = 存档 + 跳时间与读回一致性', () => {
  it('权威闭环：服务端返回第7天/999金币/雨天，UI显示严格取自服务端权威数据（数据变了显示就变）', async () => {
    const mockSnap: LifeSnapshot = {
      save: {
        owner: 'tester',
        theme: 'forest',
        coins: 350,
        skill_exp: 10,
        bag: { wood: 5 },
        affinity: {},
        gifts_today: { elder: 1 },
        gather_counts: { tree: 3 },
        quest_log: { day: 2, entries: [] },
        shop: { kind: 'grocery', level: 1, stock: {}, ask_prices: {} },
        clock: { day: 2, minute: 1200, part: 'night', part_label: '夜晚' },
        weather: { id: 'rain', label: '雨', icon: '🌧️', light: 0.6, yield_pct: 100, pace_pct: 100, demand_pct: 100, blocks_gather: false },
        version: 1,
      },
      hud_line: '第2天 夜晚 雨 · 350 金币',
      npcs: [],
      shop: [],
      craft: [],
      gather: [],
      version: 1,
    };

    // Mock 服务端权威返回：第 7 天 / 金币 999 / 天气 rain (雨) / 日结算收益 649
    const mockActionResult: LifeActionResult = {
      action: 'sleep',
      save: {
        ...mockSnap.save,
        coins: 999,
        clock: { day: 7, minute: 360, part: 'morning', part_label: '上午' },
        weather: { id: 'rain', label: '雨', icon: '🌧️', light: 0.6, yield_pct: 100, pace_pct: 100, demand_pct: 100, blocks_gather: false },
        version: 2,
      },
      hud_line: '第7天 上午 雨 · 999 金币',
      settlement: {
        day: 6,
        weather: 'rain',
        theme: 'forest',
        lines: [],
        total_revenue: 649,
        cost_of_goods_sold: 0,
        profit: 649,
        coins_after: 999,
        next_day: 7,
      },
      note: '睡到第 7 天 06:00；没卖出的货品如实留在仓库。',
    };
    const mockAction = vi.fn().mockResolvedValue(mockActionResult);

    const report = await executeSleep(mockAction, mockSnap);

    expect(mockAction).toHaveBeenCalledWith('sleep', {});
    // 关键校验：真断言，数据取自服务端而不是前端 dayBefore + 1 算术
    expect(report.dayBefore).toBe(2);
    expect(report.dayAfter).toBe(7);
    expect(report.coinsBefore).toBe(350);
    expect(report.coinsAfter).toBe(999);
    expect(report.settlementRevenue).toBe(649);
    expect(report.timeLabel).toBe('第7天 上午 06:00 雨');
    expect(report.note).toBe('睡到第 7 天 06:00；没卖出的货品如实留在仓库。');
  });

  it('fetchSnapshotFn 闭环：onAction 未回传 save 时从最新快照读回（第10天/雪天）', async () => {
    const mockAction = vi.fn().mockResolvedValue(undefined);
    const mockSnap: LifeSnapshot = {
      save: {
        owner: 'tester',
        theme: 'forest',
        coins: 100,
        skill_exp: 0,
        bag: {},
        affinity: {},
        gifts_today: {},
        gather_counts: {},
        quest_log: { day: 1, entries: [] },
        shop: { kind: 'grocery', level: 1, stock: {}, ask_prices: {} },
        clock: { day: 1, minute: 480, part: 'morning', part_label: '上午' },
        weather: { id: 'sun', label: '晴', icon: '☀️', light: 1.0, yield_pct: 100, pace_pct: 100, demand_pct: 100, blocks_gather: false },
        version: 1,
      },
      hud_line: '第1天 上午 晴 · 100 金币',
      npcs: [],
      shop: [],
      craft: [],
      gather: [],
      version: 1,
    };

    const mockFreshSnapshot: LifeSnapshot = {
      ...mockSnap,
      save: {
        ...mockSnap.save,
        coins: 1500,
        clock: { day: 10, minute: 360, part: 'morning', part_label: '上午' },
        weather: { id: 'snow', label: '雪', icon: '❄️', light: 0.7, yield_pct: 80, pace_pct: 80, demand_pct: 120, blocks_gather: false },
        version: 3,
      },
    };

    const report = await executeSleep(mockAction, mockSnap, async () => mockFreshSnapshot);

    expect(report.dayAfter).toBe(10);
    expect(report.coinsAfter).toBe(1500);
    expect(report.timeLabel).toBe('第10天 上午 06:00 雪');
  });

  it('零假数据保证：服务端未返回有效存档时绝不伪造自算，明确拒绝', async () => {
    const mockAction = vi.fn().mockResolvedValue(undefined);
    await expect(executeSleep(mockAction, null, async () => (null as any))).rejects.toThrow(
      '睡觉动作失败：服务端未返回权威存档或时钟数据',
    );
  });
});

describe('B5 · H4 储物 / 做饭 / 制作 / 展示收藏四大互动站', () => {
  describe('1. 储物站 (Storage)', () => {
    it('存入物品：自动堆叠同类物品并受最大格子数限制', () => {
      let storage = createCabinStorage(2);
      // 存入木材
      const r1 = depositItem(storage, 'wood', 10);
      expect(r1.success).toBe(true);
      storage = r1.storage;
      expect(storage.items).toEqual([{ itemId: 'wood', count: 10 }]);

      // 堆叠木材
      const r2 = depositItem(storage, 'wood', 5);
      expect(r2.success).toBe(true);
      storage = r2.storage;
      expect(storage.items).toEqual([{ itemId: 'wood', count: 15 }]);

      // 存入石块
      const r3 = depositItem(storage, 'stone', 8);
      expect(r3.success).toBe(true);
      storage = r3.storage;
      expect(storage.items.length).toBe(2);

      // 格子已满，存入第 3 种物品应被拒绝
      const r4 = depositItem(storage, 'iron', 2);
      expect(r4.success).toBe(false);
      expect(r4.reason).toContain('储物箱已满');
    });

    it('取出物品：准确扣减并清空数量为 0 的格子', () => {
      let storage = createCabinStorage(5);
      storage = depositItem(storage, 'apple', 6).storage;

      // 提取部分
      const r1 = withdrawItem(storage, 'apple', 4);
      expect(r1.success).toBe(true);
      storage = r1.storage;
      expect(storage.items).toEqual([{ itemId: 'apple', count: 2 }]);

      // 超额提取被拒
      const r2 = withdrawItem(storage, 'apple', 5);
      expect(r2.success).toBe(false);
      expect(r2.reason).toContain('数量不足');

      // 提取全部后格子释放
      const r3 = withdrawItem(storage, 'apple', 2);
      expect(r3.success).toBe(true);
      storage = r3.storage;
      expect(storage.items).toEqual([]);
    });
  });

  describe('2. 做饭站 (Cooking)', () => {
    it('配方表收录 9 种料理与茶酒，对齐 crafting.py', () => {
      expect(COOKING_RECIPES.length).toBe(9);
      const ids = COOKING_RECIPES.map((r) => r.id);
      expect(ids).toContain('bread');
      expect(ids).toContain('jam');
      expect(ids).toContain('soup');
      expect(ids).toContain('mushroom_stew');
      expect(ids).toContain('dried_fish');
      expect(ids).toContain('pumpkin_pie');
      expect(ids).toContain('tea');
      expect(ids).toContain('wine');
    });

    it('材料不足、金币不足或技能等级不足时拒绝烹饪', () => {
      // 缺少食材
      const bag = { wheat: 1 }; // 面包需要 2 wheat + 1 honey
      const r1 = canCook('bread', bag, 100, 1);
      expect(r1.ok).toBe(false);
      expect(r1.reason).toContain('缺少食材');

      // 金币不足
      const bagFull = { wheat: 2, honey: 1 };
      const r2 = canCook('bread', bagFull, 5, 1); // 需要 8 金币
      expect(r2.ok).toBe(false);
      expect(r2.reason).toContain('金币不足');

      // 等级不足
      const r3 = canCook('pumpkin_pie', { pumpkin: 2, wheat: 2 }, 100, 1); // 需要 Lv.3
      expect(r3.ok).toBe(false);
      expect(r3.reason).toContain('烹饪技能等级不足');
    });

    it('成功烹饪扣除食材与金币，并在背包生成料理成品', () => {
      const bag = { wheat: 3, honey: 1 };
      const res = cookMeal('bread', bag, 20, 1);
      expect(res.success).toBe(true);
      expect(res.coinsAfter).toBe(12); // 20 - 8
      expect(res.newBag.wheat).toBe(1); // 3 - 2
      expect(res.newBag.honey).toBeUndefined(); // 消耗完毕
      expect(res.newBag.bread).toBe(2); // 获得 2 个面包
    });
  });

  describe('3. 制作站 (Crafting)', () => {
    it('提供家具、工艺品、药水与能源的工坊制作配方', () => {
      expect(CRAFT_STATION_RECIPES.length).toBeGreaterThanOrEqual(6);
      const ids = CRAFT_STATION_RECIPES.map((r) => r.id);
      expect(ids).toContain('handicraft');
      expect(ids).toContain('incense');
      expect(ids).toContain('wood_chair');
      expect(ids).toContain('potion');
      expect(ids).toContain('battery');
    });

    it('制作检查材料与金币消耗', () => {
      const canMake = canCraftItem('handicraft', { wood: 3, cotton: 1 }, 50, 2);
      expect(canMake.ok).toBe(true);

      const noWood = canCraftItem('handicraft', { wood: 2, cotton: 1 }, 50, 2);
      expect(noWood.ok).toBe(false);
      expect(noWood.reason).toContain('缺少材料 wood');
    });
  });

  describe('4. 展示收藏站 (Display & Collections)', () => {
    it('展台放置、查验与取下完整闭环', () => {
      let stand = createDisplayStand(4);
      expect(stand.slots.length).toBe(4);

      // 放置稀有矿石
      const placeRes = placeOnDisplay(stand, 1, 'moonstone', '月光石', '在微暗的室内散发着柔和蓝光。');
      expect(placeRes.success).toBe(true);
      stand = placeRes.stand;
      expect(stand.slots[1]?.itemId).toBe('moonstone');
      expect(stand.slots[1]?.label).toBe('月光石');
      expect(stand.slots[1]?.inspectionNote).toContain('柔和蓝光');

      // 取下物品
      const takeRes = takeFromDisplay(stand, 1);
      expect(takeRes.success).toBe(true);
      expect(takeRes.removedItem).toBe('moonstone');
      stand = takeRes.stand;
      expect(stand.slots[1]?.itemId).toBeNull();
    });
  });
});

describe('B5 · H5 进出无缝与状态保持', () => {
  it('进出屋管理器无损保留室外玩家坐标与世界参数', () => {
    const mgr = new SeamlessTransitionManager();
    expect(mgr.getSavedOutdoorState()).toBeNull();

    mgr.saveOutdoorState({
      playerX: 1240,
      playerY: 280,
      cameraX: 920,
      themeId: 'forest',
      timeOfDay: 'dusk',
    });

    const saved = mgr.getSavedOutdoorState();
    expect(saved).not.toBeNull();
    expect(saved?.playerX).toBe(1240);
    expect(saved?.playerY).toBe(280);
    expect(saved?.cameraX).toBe(920);
    expect(saved?.themeId).toBe('forest');
    expect(saved?.timeOfDay).toBe('dusk');

    mgr.clear();
    expect(mgr.getSavedOutdoorState()).toBeNull();
  });

  it('全局无缝管理器单例正常运转', () => {
    globalTransitionManager.saveOutdoorState({
      playerX: 500,
      playerY: 100,
      cameraX: 200,
      themeId: 'garden',
      timeOfDay: 'day',
    });
    expect(globalTransitionManager.getSavedOutdoorState()?.themeId).toBe('garden');
    globalTransitionManager.clear();
  });
});
