import { describe, expect, it } from 'vitest';
import {
  getAllThemedWorlds,
  getEnvironmentalFeaturesForTheme,
  getInteractivePlantsForTheme,
  getThemedWorld,
  normalizeThemeId,
  convertPlantToGatherRow,
  THEMED_WORLDS,
} from './cabinThemedWorlds';

describe('cabinThemedWorlds · 大地图专属环境特征与特色交互植物 (T4.1, T4.2)', () => {
  it('正确归一化地图别名与核心 5 大地图 ID', () => {
    expect(normalizeThemeId('forest')).toBe('forest');
    expect(normalizeThemeId('garden')).toBe('garden');
    expect(normalizeThemeId('stream')).toBe('stream');
    expect(normalizeThemeId('golden_field')).toBe('golden_field');
    expect(normalizeThemeId('field')).toBe('golden_field');
    expect(normalizeThemeId('country')).toBe('golden_field');
    expect(normalizeThemeId('observatory')).toBe('observatory');
    expect(normalizeThemeId('planet')).toBe('observatory');
    expect(normalizeThemeId('scifi')).toBe('observatory');
    expect(normalizeThemeId('unknown_theme')).toBe('forest');
    expect(getThemedWorld('forest').id).toBe('forest');
    expect(THEMED_WORLDS.forest).toBeDefined();
  });

  it('获取全部 5 大主题世界，且每个主题具有独特的场景美学与专属 NPC', () => {
    const allWorlds = getAllThemedWorlds();
    expect(allWorlds).toHaveLength(5);

    const themeIds = allWorlds.map((w) => w.id);
    expect(themeIds).toContain('forest');
    expect(themeIds).toContain('garden');
    expect(themeIds).toContain('golden_field');
    expect(themeIds).toContain('stream');
    expect(themeIds).toContain('observatory');

    for (const world of allWorlds) {
      expect(world.name).toBeTruthy();
      expect(world.title).toBeTruthy();
      expect(world.description).toBeTruthy();
      expect(world.exclusiveNpcId).toBeTruthy();
      expect(world.exclusiveNpcName).toBeTruthy();
      expect(world.features.length).toBeGreaterThanOrEqual(4);
      expect(world.interactivePlants.length).toBeGreaterThanOrEqual(2);
      expect(world.specialties.length).toBeGreaterThanOrEqual(3);
    }
  });

  describe('各主题专属环境特征 (T4.1)', () => {
    it('老林子 (forest) 包含晨雾古树、红白蘑菇丛、原木花桩、溪泉清风', () => {
      const features = getEnvironmentalFeaturesForTheme('forest');
      const names = features.map((f) => f.name);
      expect(names).toContain('晨雾古树');
      expect(names).toContain('红白蘑菇丛');
      expect(names).toContain('原木花桩');
      expect(names).toContain('溪泉清风');
    });

    it('后花园 (garden) 包含白石喷泉、玫瑰花廊、紫藤花架、绿荫石阶、茶歇遮阳伞', () => {
      const features = getEnvironmentalFeaturesForTheme('garden');
      const names = features.map((f) => f.name);
      expect(names).toContain('白石喷泉');
      expect(names).toContain('玫瑰花廊');
      expect(names).toContain('紫藤花架');
      expect(names).toContain('绿荫石阶');
      expect(names).toContain('茶歇遮阳伞');
    });

    it('黄金田野 (golden_field / field) 包含旋转大风车、金黄麦浪、稻草垛、南瓜堆', () => {
      const features = getEnvironmentalFeaturesForTheme('field');
      const names = features.map((f) => f.name);
      expect(names).toContain('旋转大风车');
      expect(names).toContain('金黄麦浪');
      expect(names).toContain('稻草垛');
      expect(names).toContain('南瓜堆');
    });

    it('溪水边 (stream) 包含木质垂钓栈桥、河畔鹅卵石、摇曳芦苇丛、粉白睡莲', () => {
      const features = getEnvironmentalFeaturesForTheme('stream');
      const names = features.map((f) => f.name);
      expect(names).toContain('木质垂钓栈桥');
      expect(names).toContain('河畔鹅卵石');
      expect(names).toContain('摇曳芦苇丛');
      expect(names).toContain('粉白睡莲');
    });

    it('观星台 (observatory / planet) 包含古代星盘、黄铜天文望远镜、星座地垫、流星划过', () => {
      const features = getEnvironmentalFeaturesForTheme('planet');
      const names = features.map((f) => f.name);
      expect(names).toContain('古代星盘');
      expect(names).toContain('黄铜天文望远镜');
      expect(names).toContain('星座地垫');
      expect(names).toContain('流星划过');
    });
  });

  describe('各主题特色交互植物与采集物 (T4.2)', () => {
    it('老林子包含「灵芝仙菇」与「薄荷野草」采摘', () => {
      const plants = getInteractivePlantsForTheme('forest');
      const ids = plants.map((p) => p.id);
      expect(ids).toContain('forest_lingzhi');
      expect(ids).toContain('forest_wild_mint');

      const mushroom = plants.find((p) => p.id === 'forest_lingzhi');
      expect(mushroom?.name).toBe('灵芝仙菇');
      expect(mushroom?.action).toBe('pick');
      expect(mushroom?.actionLabel).toBe('采摘');
    });

    it('后花园包含「香水大马士革玫瑰」「晨露茉莉」采摘与「斑斓彩蝶」捕逗', () => {
      const plants = getInteractivePlantsForTheme('garden');
      const ids = plants.map((p) => p.id);
      expect(ids).toContain('garden_damask_rose');
      expect(ids).toContain('garden_dew_jasmine');
      expect(ids).toContain('garden_butterfly');

      const butterfly = plants.find((p) => p.id === 'garden_butterfly');
      expect(butterfly?.action).toBe('collect');
      expect(butterfly?.actionLabel).toBe('捕逗');
    });

    it('黄金田野包含收获「金黄丰收麦穗」与「蜜糖大南瓜」', () => {
      const plants = getInteractivePlantsForTheme('golden_field');
      const ids = plants.map((p) => p.id);
      expect(ids).toContain('field_golden_wheat');
      expect(ids).toContain('field_sugar_pumpkin');

      const wheat = plants.find((p) => p.id === 'field_golden_wheat');
      expect(wheat?.action).toBe('harvest');
      expect(wheat?.actionLabel).toBe('收割');
    });

    it('溪水边包含采摘「幽香睡莲」与「嫩绿水芹」', () => {
      const plants = getInteractivePlantsForTheme('stream');
      const ids = plants.map((p) => p.id);
      expect(ids).toContain('stream_fragrant_waterlily');
      expect(ids).toContain('stream_tender_cress');

      const lily = plants.find((p) => p.id === 'stream_fragrant_waterlily');
      expect(lily?.action).toBe('pick');
      expect(lily?.actionLabel).toBe('采摘');
    });

    it('观星台包含抚触「星象仪」与收集「星芒碎片」', () => {
      const plants = getInteractivePlantsForTheme('observatory');
      const ids = plants.map((p) => p.id);
      expect(ids).toContain('observatory_armillary');
      expect(ids).toContain('observatory_star_shards');

      const sphere = plants.find((p) => p.id === 'observatory_armillary');
      expect(sphere?.action).toBe('touch');
      expect(sphere?.actionLabel).toBe('抚触');
    });
  });

  describe('convertPlantToGatherRow 适配器', () => {
    it('正确将交互植物转为生命模拟 GatherRow，计算距离与在范围标志', () => {
      const plants = getInteractivePlantsForTheme('forest');
      const mushroom = plants[0];
      expect(mushroom).toBeDefined();

      // 当玩家在植物所在瓦片附近 (如 [18, 52]，植物默认在 [18, 52])
      const rowNear = convertPlantToGatherRow(mushroom, [18, 52]);
      expect(rowNear.id).toBe(mushroom.id);
      expect(rowNear.label).toBe(mushroom.name);
      expect(rowNear.action).toBe(mushroom.action);
      expect(rowNear.distance).toBe(0);
      expect(rowNear.in_range).toBe(true);

      // 当玩家距离植物较远 (如 [0, 0])
      const rowFar = convertPlantToGatherRow(mushroom, [0, 0]);
      expect(rowFar.distance).toBeGreaterThan(2);
      expect(rowFar.in_range).toBe(false);
    });
  });
});
