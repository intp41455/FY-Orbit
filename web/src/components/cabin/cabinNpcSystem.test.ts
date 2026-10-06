import { beforeEach, describe, expect, it } from 'vitest';
import {
  EXCLUSIVE_NPCS,
  getAllExclusiveNpcs,
  getExclusiveNpcByTheme,
  getNpcState,
  interactWithNpc,
  giveGiftToNpc,
  convertNpcToNpcRow,
  resolveNpcMicroDialogue,
  MAX_GIFTS_PER_DAY,
} from './cabinNpcSystem';

describe('cabinNpcSystem · 全地图专属手绘 NPC 与互动好感系统 (T5.1, T5.2)', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('5 大地图专属手绘 NPC 元数据与外观 (T5.1)', () => {
    it('定义了全部 5 大地图的专属手绘 NPC，每个 NPC 均有手绘外观描述与双帧像素矩阵', () => {
      const npcs = getAllExclusiveNpcs();
      expect(npcs).toHaveLength(5);

      const npcIds = npcs.map((n) => n.id);
      expect(npcIds).toContain('forest_elder_pine');
      expect(npcIds).toContain('garden_fairy_ellie');
      expect(npcIds).toContain('field_scarecrow_farmer');
      expect(npcIds).toContain('stream_fisherman_hermit');
      expect(npcIds).toContain('observatory_celeste');

      for (const npc of npcs) {
        expect(npc.name).toBeTruthy();
        expect(npc.title).toBeTruthy();
        expect(npc.role).toBeTruthy();
        expect(npc.appearanceDescription).toBeTruthy();
        expect(npc.loreAndKnowledge).toBeTruthy();

        // 像素艺术画矩阵检验：2 帧，每帧 24 行，每行 16 列
        expect(npc.pixelFrames).toHaveLength(2);
        for (const frame of npc.pixelFrames) {
          expect(frame).toHaveLength(24);
          for (const row of frame) {
            expect(row).toHaveLength(16);
          }
        }
        expect(Object.keys(npc.pixelPalette).length).toBeGreaterThanOrEqual(10);
      }
    });

    it('老林子 NPC 为【守林隐士 · 青松老者】', () => {
      const npc = getExclusiveNpcByTheme('forest');
      expect(npc.name).toBe('青松老者');
      expect(npc.title).toBe('守林隐士');
      expect(npc.appearanceDescription).toContain('草蓑');
      expect(npc.appearanceDescription).toContain('木杖');
    });

    it('后花园 NPC 为【花艺精灵少女 · 艾莉】', () => {
      const npc = getExclusiveNpcByTheme('garden');
      expect(npc.name).toBe('艾莉');
      expect(npc.title).toBe('花艺精灵少女');
      expect(npc.appearanceDescription).toContain('向日葵草帽');
      expect(npc.appearanceDescription).toContain('围裙');
    });

    it('黄金田野 NPC 为【麦田守望者 · 麦芒叔】', () => {
      const npc = getExclusiveNpcByTheme('golden_field');
      expect(npc.name).toBe('麦芒叔');
      expect(npc.title).toBe('麦田守望者');
      expect(npc.appearanceDescription).toContain('尖顶');
    });

    it('溪水边 NPC 为【碧水钓叟 · 渔隐客】', () => {
      const npc = getExclusiveNpcByTheme('stream');
      expect(npc.name).toBe('渔隐客');
      expect(npc.title).toBe('碧水钓叟');
      expect(npc.appearanceDescription).toContain('码头');
    });

    it('观星台 NPC 为【观星学者 · 塞莱斯特】', () => {
      const npc = getExclusiveNpcByTheme('observatory');
      expect(npc.name).toBe('塞莱斯特');
      expect(npc.title).toBe('观星学者');
      expect(npc.appearanceDescription).toContain('星纹法袍');
    });
  });

  describe('微表情台词池解析 (T5.2)', () => {
    it('支持 smile, ponder, joy, teach, care, mystic, greet 多种微表情', () => {
      const expressions = ['smile', 'ponder', 'joy', 'teach', 'care', 'mystic', 'greet'] as const;
      for (const expr of expressions) {
        const dial = resolveNpcMicroDialogue('forest_elder_pine', { expression: expr });
        expect(dial.expression).toBe(expr);
        expect(dial.text).toBeTruthy();
        expect(dial.emoji).toBeTruthy();
      }
    });
  });

  describe('NPC 互动与好感度逻辑 (T5.2)', () => {
    it('闲聊、请教与打招呼增加好感度', () => {
      const resChat = interactWithNpc('forest_elder_pine', 'chat', 1);
      expect(resChat.gainedPoints).toBe(15);
      expect(resChat.state.points).toBe(15);
      expect(resChat.dialogue.text).toBeTruthy();

      const resLore = interactWithNpc('forest_elder_pine', 'ask_lore', 1);
      expect(resLore.gainedPoints).toBe(20);
      expect(resLore.state.points).toBe(35);

      const resGreet = interactWithNpc('forest_elder_pine', 'pat_greet', 1);
      expect(resGreet.gainedPoints).toBe(10);
      expect(resGreet.state.points).toBe(45);
    });

    it('多次互动持续累加好感并计算心数', () => {
      for (let i = 0; i < 7; i++) {
        interactWithNpc('garden_fairy_ellie', 'ask_lore', 1);
      }
      const state = getNpcState('garden_fairy_ellie');
      expect(state.points).toBe(140);
      expect(state.hearts).toBe(1);
    });
  });

  describe('每日赠礼系统与喜好判定 (T5.2)', () => {
    it('赠送最爱礼物 (+50) 触发 loved 独家答谢台词', () => {
      const npc = EXCLUSIVE_NPCS.forest_elder_pine;
      const lovedGift = npc.gifts.loved[0]; // e.g. 'lingzhi'
      const res = giveGiftToNpc('forest_elder_pine', lovedGift, '灵芝仙菇', 1);
      expect(res.success).toBe(true);
      expect(res.gainedPoints).toBe(50);
      expect(res.reactionType).toBe('loved');
      expect(res.responseText).toContain(npc.gifts.thankLines.loved);
    });

    it('赠送喜欢礼物 (+35) 触发 liked 答谢台词', () => {
      const npc = EXCLUSIVE_NPCS.forest_elder_pine;
      const likedGift = npc.gifts.liked[0]; // e.g. 'mint_herb'
      const res = giveGiftToNpc('forest_elder_pine', likedGift, '薄荷野草', 1);
      expect(res.success).toBe(true);
      expect(res.gainedPoints).toBe(35);
      expect(res.reactionType).toBe('liked');
      expect(res.responseText).toContain(npc.gifts.thankLines.liked);
    });

    it('赠送厌恶礼物 (+5) 触发 disliked 答谢台词', () => {
      const npc = EXCLUSIVE_NPCS.forest_elder_pine;
      const dislikedGift = npc.gifts.disliked[0]; // e.g. 'alloy_ore'
      const res = giveGiftToNpc('forest_elder_pine', dislikedGift, '合金矿', 1);
      expect(res.success).toBe(true);
      expect(res.gainedPoints).toBe(5);
      expect(res.reactionType).toBe('disliked');
      expect(res.responseText).toContain(npc.gifts.thankLines.disliked);
    });

    it('每日赠礼达到 3 次上限后拒绝赠礼并防刷', () => {
      giveGiftToNpc('observatory_celeste', 'star_shards', '星芒碎片', 1);
      giveGiftToNpc('observatory_celeste', 'star_shards', '星芒碎片', 1);
      giveGiftToNpc('observatory_celeste', 'star_shards', '星芒碎片', 1);

      const res4 = giveGiftToNpc('observatory_celeste', 'star_shards', '星芒碎片', 1);
      expect(res4.success).toBe(false);
      expect(res4.reason).toBe('今日赠礼次数已用完');
      expect(res4.state.giftsToday).toBe(MAX_GIFTS_PER_DAY);
    });
  });

  describe('convertNpcToNpcRow 转换器', () => {
    it('正确将专属 NPC 元数据转换为 HUD 兼容 NpcRow 规格', () => {
      const npc = EXCLUSIVE_NPCS.stream_fisherman_hermit;
      const row = convertNpcToNpcRow(npc, {
        npcId: npc.id,
        points: 350,
        hearts: 3,
        giftsToday: 0,
        lastGiftDay: 1,
        interactCount: 5,
      });

      expect(row.id).toBe(npc.id);
      expect(row.name).toBe('渔隐客（碧水钓叟）');
      expect(row.hearts).toBe(3);
      expect(row.hearts_display).toBe('♥♥♥♡♡♡♡♡♡♡');
      expect(row.place).toBe(npc.themeLabel);
    });
  });
});
