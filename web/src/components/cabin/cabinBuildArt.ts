/**
 * B4 · 建造与装修家具像素美术（数据源严格对齐 build.py 28 件家具 CATALOG）。
 *
 * 1. 28 件家具完整清单（6 分类）：
 *    - table_chair (4): b4_dining_table (2×2), b4_stool (1×1), b4_tea_table (2×1), b4_writing_desk (3×1)
 *    - bed (4): b4_single_bed (2×3), b4_double_bed (3×3), b4_bunk_bed (2×4), b4_hammock (2×1)
 *    - cabinet (4): b4_wardrobe (2×1), b4_shelf (1×2), b4_drawer (2×1), b4_coatrack (1×1)
 *    - decor (6): b4_rug_small (2×2 under), b4_rug_long (3×1 under), b4_painting (1×1 wall),
 *                 b4_wall_clock (1×1 wall), b4_mirror_small (1×1 wall), b4_rug_large (3×3 under)
 *    - lamp (5): b4_ceiling_lamp (2×1 floor), b4_floor_lamp (1×2 floor), b4_desk_lamp (1×1 floor),
 *                b4_lantern (1×1 wall), b4_candle (1×1 floor)
 *    - plant (5): b4_pot_plant (1×1 floor), b4_tall_plant (1×2 floor), b4_hanging_vine (1×1 wall),
 *                 b4_herb_box (2×1 floor), b4_bonsai (1×1 floor)
 * 2. 占位与像素定标：
 *    - 网格基于 TILE = 32px；
 *    - 支持四向旋转（0, 90, 180, 270）与尺寸变换；
 *    - 纹理单例按 (id, rotation) 缓存，零逐帧重复开销。
 */

import { TILE } from './cabinConfig';
import { shade } from './cabinPixelArt';
import { PixelBuffer, bufferToTexture, type PixelTexture } from './cabinPixels';
import type { Category, FurnitureDef, PlacedItem, Rotation } from './gameplay/buildApi';

/** 28 件家具全量目录（严格同步 build.py:CATALOG） */
export const B4_CATALOG: readonly FurnitureDef[] = [
  // ---- 桌椅 (table_chair) ----
  { id: 'b4_dining_table', label: '方桌', category: 'table_chair', layer: 'floor', width: 2, height: 2, cost: 120, comfort: 4 },
  { id: 'b4_stool', label: '小凳', category: 'table_chair', layer: 'floor', width: 1, height: 1, cost: 30, comfort: 1 },
  { id: 'b4_tea_table', label: '矮茶几', category: 'table_chair', layer: 'floor', width: 2, height: 1, cost: 80, comfort: 3 },
  { id: 'b4_writing_desk', label: '书桌', category: 'table_chair', layer: 'floor', width: 3, height: 1, cost: 160, comfort: 5 },
  // ---- 床 (bed) ----
  { id: 'b4_single_bed', label: '单人床', category: 'bed', layer: 'floor', width: 2, height: 3, cost: 240, comfort: 6 },
  { id: 'b4_double_bed', label: '双人床', category: 'bed', layer: 'floor', width: 3, height: 3, cost: 420, comfort: 8 },
  { id: 'b4_bunk_bed', label: '上下铺', category: 'bed', layer: 'floor', width: 2, height: 4, cost: 380, comfort: 6 },
  { id: 'b4_hammock', label: '吊床', category: 'bed', layer: 'floor', width: 2, height: 1, cost: 150, comfort: 4 },
  // ---- 柜 (cabinet) ----
  { id: 'b4_wardrobe', label: '衣柜', category: 'cabinet', layer: 'floor', width: 2, height: 1, cost: 300, comfort: 5 },
  { id: 'b4_shelf', label: '置物架', category: 'cabinet', layer: 'floor', width: 1, height: 2, cost: 140, comfort: 3 },
  { id: 'b4_drawer', label: '抽屉柜', category: 'cabinet', layer: 'floor', width: 2, height: 1, cost: 170, comfort: 3 },
  { id: 'b4_coatrack', label: '衣帽架', category: 'cabinet', layer: 'floor', width: 1, height: 1, cost: 90, comfort: 2 },
  // ---- 装饰 (decor) ----
  { id: 'b4_rug_small', label: '小地毯', category: 'decor', layer: 'under', width: 2, height: 2, cost: 60, comfort: 3 },
  { id: 'b4_rug_long', label: '长地毯', category: 'decor', layer: 'under', width: 3, height: 1, cost: 90, comfort: 4 },
  { id: 'b4_painting', label: '挂画', category: 'decor', layer: 'wall', width: 1, height: 1, cost: 110, comfort: 3 },
  { id: 'b4_wall_clock', label: '挂钟', category: 'decor', layer: 'wall', width: 1, height: 1, cost: 70, comfort: 2 },
  { id: 'b4_mirror_small', label: '穿衣镜', category: 'decor', layer: 'wall', width: 1, height: 1, cost: 130, comfort: 3 },
  { id: 'b4_rug_large', label: '大块地毯', category: 'decor', layer: 'under', width: 3, height: 3, cost: 180, comfort: 6 },
  // ---- 灯具 (lamp) ----
  { id: 'b4_ceiling_lamp', label: '吊灯', category: 'lamp', layer: 'floor', width: 2, height: 1, cost: 200, comfort: 4 },
  { id: 'b4_floor_lamp', label: '落地灯', category: 'lamp', layer: 'floor', width: 1, height: 2, cost: 120, comfort: 3 },
  { id: 'b4_desk_lamp', label: '台灯', category: 'lamp', layer: 'floor', width: 1, height: 1, cost: 60, comfort: 2 },
  { id: 'b4_lantern', label: '纸灯笼', category: 'lamp', layer: 'wall', width: 1, height: 1, cost: 80, comfort: 2 },
  { id: 'b4_candle', label: '烛台', category: 'lamp', layer: 'floor', width: 1, height: 1, cost: 40, comfort: 1 },
  // ---- 植物 (plant) ----
  { id: 'b4_pot_plant', label: '盆栽', category: 'plant', layer: 'floor', width: 1, height: 1, cost: 50, comfort: 2 },
  { id: 'b4_tall_plant', label: '高盆栽', category: 'plant', layer: 'floor', width: 1, height: 2, cost: 95, comfort: 3 },
  { id: 'b4_hanging_vine', label: '垂吊绿萝', category: 'plant', layer: 'wall', width: 1, height: 1, cost: 70, comfort: 2 },
  { id: 'b4_herb_box', label: '香草箱', category: 'plant', layer: 'floor', width: 2, height: 1, cost: 85, comfort: 3 },
  { id: 'b4_bonsai', label: '小盆景', category: 'plant', layer: 'floor', width: 1, height: 1, cost: 160, comfort: 4 },
];

export const B4_CATALOG_MAP: Map<string, FurnitureDef> = new Map(B4_CATALOG.map((f) => [f.id, f]));

/** 分类代表色（主色 / 高光 / 阴影） */
const CATEGORY_COLORS: Record<Category, { main: number; highlight: number; shadow: number }> = {
  table_chair: { main: 0x8d6748, highlight: 0xa98260, shadow: 0x5a3e26 },
  bed: { main: 0x4a6984, highlight: 0x688fa8, shadow: 0x2b3e52 },
  cabinet: { main: 0x7a583e, highlight: 0x967054, shadow: 0x4d3420 },
  decor: { main: 0xb55353, highlight: 0xd97575, shadow: 0x733030 },
  lamp: { main: 0xe6b840, highlight: 0xffe680, shadow: 0x99751a },
  plant: { main: 0x448855, highlight: 0x66bb77, shadow: 0x245533 },
};

/** 缓存纹理 */
const FURNITURE_TEXTURE_CACHE = new Map<string, PixelTexture>();


/**
 * 床类家具像素画：床架 + 床垫 + 枕头 + 被子。
 * 取代旧的纯色方块绘制，使床在场景中可辨识为家具而非色块。
 */
function drawBed(buf: PixelBuffer, w: number, h: number, colors: { main: number; highlight: number; shadow: number }): void {
  const frameH = Math.max(6, Math.round(h * 0.12));
  const mattressTop = frameH + 2;
  const mattressH = h - mattressTop - 2;

  // 床架（底部 + 两侧立柱）
  for (let y = h - frameH; y < h; y++) {
    for (let x = 2; x < w - 2; x++) {
      buf.setPx(x, y, colors.shadow, 1);
    }
  }
  for (let y = 2; y < h - frameH; y++) {
    buf.setPx(2, y, colors.shadow, 1);
    buf.setPx(3, y, colors.shadow, 0.85);
    buf.setPx(w - 3, y, colors.shadow, 1);
    buf.setPx(w - 4, y, colors.shadow, 0.85);
  }
  // 床头板
  for (let y = 2; y < Math.round(h * 0.35); y++) {
    for (let x = 2; x < w - 2; x++) {
      buf.setPx(x, y, colors.main, 1);
    }
  }
  for (let x = 2; x < w - 2; x++) {
    buf.setPx(x, 2, colors.highlight, 1);
  }

  // 床垫
  for (let y = mattressTop; y < h - 2; y++) {
    for (let x = 4; x < w - 4; x++) {
      buf.setPx(x, y, colors.highlight, 1);
    }
  }
  // 床垫顶部高光
  for (let x = 4; x < w - 4; x++) {
    buf.setPx(x, mattressTop, 0xffffff, 0.35);
  }

  // 枕头（床头区域）
  const pillowH = Math.max(4, Math.round(mattressH * 0.22));
  const pillowW = Math.max(8, Math.round((w - 12) * 0.4));
  const pillowX = 6;
  const pillowY = mattressTop + 2;
  for (let y = pillowY; y < pillowY + pillowH; y++) {
    for (let x = pillowX; x < pillowX + pillowW; x++) {
      buf.setPx(x, y, 0xf0f4f8, 1);
    }
  }
  for (let x = pillowX; x < pillowX + pillowW; x++) {
    buf.setPx(x, pillowY, 0xffffff, 0.6);
  }

  // 被子（床尾区域，覆盖床垫下半部）
  const blanketTop = pillowY + pillowH + 2;
  for (let y = blanketTop; y < h - 3; y++) {
    for (let x = 4; x < w - 4; x++) {
      buf.setPx(x, y, colors.main, 1);
    }
  }
  // 被子褶皱
  const foldColor = shade(colors.main, 0.82);
  for (let y = blanketTop + 2; y < h - 3; y += 4) {
    for (let x = 4; x < w - 4; x++) {
      buf.setPx(x, y, foldColor, 0.5);
    }
  }
  // 被子边缘高光
  for (let x = 4; x < w - 4; x++) {
    buf.setPx(x, blanketTop, colors.highlight, 0.8);
  }

  // 外圈轮廓
  for (let x = 1; x < w - 1; x++) {
    buf.setPx(x, 1, 0x221a14, 0.9);
    buf.setPx(x, h - 2, 0x221a14, 0.9);
  }
  for (let y = 1; y < h - 1; y++) {
    buf.setPx(1, y, 0x221a14, 0.9);
    buf.setPx(w - 2, y, 0x221a14, 0.9);
  }
}

/**
 * 为指定家具生成像素纹理（按格数缩放与旋转）。
 */
export function createFurnitureTexture(def: FurnitureDef, rotation: Rotation = 0): PixelTexture {
  const cacheKey = `${def.id}:${rotation}`;
  const cached = FURNITURE_TEXTURE_CACHE.get(cacheKey);
  if (cached) return cached;

  const isRotated = rotation === 90 || rotation === 270;
  const gridW = isRotated ? def.height : def.width;
  const gridH = isRotated ? def.width : def.height;
  const pixelW = gridW * TILE;
  const pixelH = gridH * TILE;

  const buf = new PixelBuffer(pixelW, pixelH);
  const colors = CATEGORY_COLORS[def.category] ?? CATEGORY_COLORS.table_chair;

  if (def.layer === 'under') {
    // 地毯类（平铺质感 + 1px 流苏饰边）
    buf.rect(0, 0, pixelW, pixelH, colors.main, 0.92);
    // 边框花纹
    for (let x = 0; x < pixelW; x += 1) {
      buf.setPx(x, 0, colors.highlight, 1);
      buf.setPx(x, pixelH - 1, colors.shadow, 1);
      if (x % 4 === 0) {
        buf.setPx(x, 1, colors.shadow, 0.7);
        buf.setPx(x, pixelH - 2, colors.highlight, 0.7);
      }
    }
    for (let y = 0; y < pixelH; y += 1) {
      buf.setPx(0, y, colors.highlight, 1);
      buf.setPx(pixelW - 1, y, colors.shadow, 1);
    }
  } else if (def.category === 'plant') {
    // 植物花盆与绿叶簇
    const potH = Math.max(8, Math.floor(pixelH * 0.35));
    const potW = Math.max(12, Math.floor(pixelW * 0.65));
    const potX = Math.floor((pixelW - potW) / 2);
    const potY = pixelH - potH;

    // 陶盆
    for (let y = potY; y < pixelH; y += 1) {
      for (let x = potX; x < potX + potW; x += 1) {
        buf.setPx(x, y, 0xb86542, 1);
      }
    }
    // 盆口高光
    for (let x = potX; x < potX + potW; x += 1) {
      buf.setPx(x, potY, 0xd98562, 1);
    }
    // 绿色枝叶团
    for (let y = 4; y < potY + 2; y += 1) {
      for (let x = 3; x < pixelW - 3; x += 1) {
        const d = (x * 7 + y * 13) % 17;
        if (d < 12) {
          buf.setPx(x, y, d % 2 === 0 ? colors.highlight : colors.main, 1);
        }
      }
    }
  } else if (def.category === 'lamp') {
    // 灯具（发光质感与支架）
    const cx = Math.floor(pixelW / 2);
    // 支架底座
    for (let y = pixelH - 6; y < pixelH; y += 1) {
      for (let x = cx - 6; x <= cx + 6; x += 1) {
        buf.setPx(x, y, 0x4a4a4a, 1);
      }
    }
    // 立柱
    for (let y = 10; y < pixelH - 6; y += 1) {
      buf.setPx(cx - 1, y, 0x6e6e6e, 1);
      buf.setPx(cx, y, 0x8a8a8a, 1);
    }
    // 灯罩与暖光
    for (let y = 2; y < 12; y += 1) {
      for (let x = cx - 10; x <= cx + 10; x += 1) {
        buf.setPx(x, y, colors.highlight, 0.95);
      }
    }
  } else if (def.category === 'bed') {
    drawBed(buf, pixelW, pixelH, colors);
  } else {
    // 桌椅 / 柜体（厚重实木质感）
    // 主体方框
    for (let y = 2; y < pixelH - 2; y += 1) {
      for (let x = 2; x < pixelW - 2; x += 1) {
        buf.setPx(x, y, colors.main, 1);
      }
    }
    // 顶部与左侧 2px 高光
    for (let x = 2; x < pixelW - 2; x += 1) {
      buf.setPx(x, 2, colors.highlight, 1);
      buf.setPx(x, 3, colors.highlight, 0.85);
    }
    for (let y = 2; y < pixelH - 2; y += 1) {
      buf.setPx(2, y, colors.highlight, 1);
      buf.setPx(3, y, colors.highlight, 0.85);
    }
    // 底部与右侧 2px 深色阴影
    for (let x = 2; x < pixelW - 2; x += 1) {
      buf.setPx(x, pixelH - 3, colors.shadow, 1);
      buf.setPx(x, pixelH - 4, colors.shadow, 0.85);
    }
    for (let y = 2; y < pixelH - 2; y += 1) {
      buf.setPx(pixelW - 3, y, colors.shadow, 1);
      buf.setPx(pixelW - 4, y, colors.shadow, 0.85);
    }
    // 外圈 1px 暗色轮廓线
    for (let x = 1; x < pixelW - 1; x += 1) {
      buf.setPx(x, 1, 0x221a14, 0.9);
      buf.setPx(x, pixelH - 2, 0x221a14, 0.9);
    }
    for (let y = 1; y < pixelH - 1; y += 1) {
      buf.setPx(1, y, 0x221a14, 0.9);
      buf.setPx(pixelW - 2, y, 0x221a14, 0.9);
    }
  }

  const texture = bufferToTexture(buf, { label: `furn-${cacheKey}` });
  FURNITURE_TEXTURE_CACHE.set(cacheKey, texture);
  return texture;
}

/**
 * 默认温馨庭院与居室摆件样例（来自 build.py 真实家具）。
 */
export const DEFAULT_PLACED_FURNITURE: readonly PlacedItem[] = [
  { id: 'inst_desk', furniture_id: 'b4_writing_desk', x: 74, y: 51, rotation: 0 },
  { id: 'inst_stool', furniture_id: 'b4_stool', x: 76, y: 52, rotation: 0 },
  { id: 'inst_bed', furniture_id: 'b4_single_bed', x: 72, y: 50, rotation: 0 },
  { id: 'inst_rug', furniture_id: 'b4_rug_small', x: 70, y: 51, rotation: 0 },
  { id: 'inst_lamp', furniture_id: 'b4_floor_lamp', x: 78, y: 50, rotation: 0 },
  { id: 'inst_plant', furniture_id: 'b4_pot_plant', x: 79, y: 51, rotation: 0 },
];
