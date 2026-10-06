import {
  Assets,
  Container,
  Graphics,
  Rectangle,
  Sprite,
  Texture,
} from 'pixi.js';
import {
  type CabinHouseId,
} from './cabinConfig';

/**
 * Tiny Swords (CC0 1.0 Universal) 高精微像素手绘资产渲染管线。
 * 将高精微像素手绘建筑、植被、角色、萌宠与自然光影无缝注入 PixiJS 主场景。
 */

export interface TinySwordsAssets {
  houseYellow: Texture;
  houseBlue: Texture;
  tree: Texture;
  tilemapFlat: Texture;
  pawnYellow: Texture;
  pawnBlue: Texture;
  sheep: Texture;
  deco01: Texture; // Bush
  deco02: Texture; // Small bush
  deco03: Texture; // Wildflowers
  deco07: Texture; // Mushroom
  deco08: Texture; // Red mushroom
  deco16: Texture; // Signpost
  deco17: Texture; // Wooden fence
}

let cachedAssets: TinySwordsAssets | null = null;
let assetLoadingPromise: Promise<TinySwordsAssets | null> | null = null;

/**
 * 安全加载单张纹理，自动设置 nearest 采样
 */
async function loadNearestTexture(path: string): Promise<Texture | null> {
  if (typeof window === 'undefined' || typeof Image === 'undefined') return null;
  try {
    const tex = await Assets.load<Texture>(path);
    if (tex && tex.source) {
      tex.source.scaleMode = 'nearest';
    }
    return tex;
  } catch (err) {
    console.warn(`[TinySwordsArt] Failed to load ${path}:`, err);
    return null;
  }
}

/**
 * 异步预加载 Tiny Swords 全套核心 CC0 资源
 */
export async function loadTinySwordsAssets(): Promise<TinySwordsAssets | null> {
  if (cachedAssets) return cachedAssets;
  if (assetLoadingPromise) return assetLoadingPromise;

  assetLoadingPromise = (async () => {
    try {
      const basePath = '/assets/packs/tiny_swords';
      const [
        houseYellow,
        houseBlue,
        tree,
        tilemapFlat,
        pawnYellow,
        pawnBlue,
        sheep,
        deco01,
        deco02,
        deco03,
        deco07,
        deco08,
        deco16,
        deco17,
      ] = await Promise.all([
        loadNearestTexture(`${basePath}/Houses/House_Yellow.png`),
        loadNearestTexture(`${basePath}/Houses/House_Blue.png`),
        loadNearestTexture(`${basePath}/Trees/Tree.png`),
        loadNearestTexture(`${basePath}/Terrain/Tilemap_Flat.png`),
        loadNearestTexture(`${basePath}/Characters/Pawn_Yellow.png`),
        loadNearestTexture(`${basePath}/Characters/Pawn_Blue.png`),
        loadNearestTexture(`${basePath}/Sheep/HappySheep_Idle.png`),
        loadNearestTexture(`${basePath}/Deco/01.png`),
        loadNearestTexture(`${basePath}/Deco/02.png`),
        loadNearestTexture(`${basePath}/Deco/03.png`),
        loadNearestTexture(`${basePath}/Deco/07.png`),
        loadNearestTexture(`${basePath}/Deco/08.png`),
        loadNearestTexture(`${basePath}/Deco/16.png`),
        loadNearestTexture(`${basePath}/Deco/17.png`),
      ]);

      if (!houseYellow || !tree || !tilemapFlat || !pawnYellow || !sheep) {
        console.warn('[TinySwordsArt] Some essential textures failed to load');
        return null;
      }

      cachedAssets = {
        houseYellow,
        houseBlue: houseBlue ?? houseYellow,
        tree,
        tilemapFlat,
        pawnYellow,
        pawnBlue: pawnBlue ?? pawnYellow,
        sheep,
        deco01: deco01 ?? houseYellow,
        deco02: deco02 ?? houseYellow,
        deco03: deco03 ?? houseYellow,
        deco07: deco07 ?? houseYellow,
        deco08: deco08 ?? houseYellow,
        deco16: deco16 ?? houseYellow,
        deco17: deco17 ?? houseYellow,
      };

      return cachedAssets;
    } catch (e) {
      console.warn('[TinySwordsArt] Error loading assets:', e);
      return null;
    } finally {
      assetLoadingPromise = null;
    }
  })();

  return assetLoadingPromise;
}

export function getLoadedTinySwordsAssets(): TinySwordsAssets | null {
  return cachedAssets;
}

/**
 * 切分子纹理切片（帧）
 */
export function extractSubTexture(
  base: Texture,
  x: number,
  y: number,
  w: number,
  h: number,
): Texture {
  return new Texture({
    source: base.source,
    frame: new Rectangle(x, y, w, h),
  });
}

/**
 * 提取 Pawn 角色动画切片（6 帧待机 + 6 帧行走）
 * 每个原始网格 192×192，内部中心是手绘角色
 */
export function getPawnFrames(pawnTexture: Texture): {
  idle: Texture[];
  walk: Texture[];
} {
  const idle: Texture[] = [];
  const walk: Texture[] = [];
  for (let col = 0; col < 6; col++) {
    // Row 0 = Idle
    idle.push(extractSubTexture(pawnTexture, col * 192, 0, 192, 192));
    // Row 1 = Walk
    walk.push(extractSubTexture(pawnTexture, col * 192, 192, 192, 192));
  }
  return { idle, walk };
}

/**
 * 提取 Happy Sheep 动画切片（8 帧待机咀嚼/轻晃）
 * 每个原始网格 128×128
 */
export function getSheepFrames(sheepTexture: Texture): Texture[] {
  const frames: Texture[] = [];
  for (let col = 0; col < 8; col++) {
    frames.push(extractSubTexture(sheepTexture, col * 128, 0, 128, 128));
  }
  return frames;
}

/**
 * 构建高精手绘老林子全套微像素环境图层
 */
export class TinySwordsEnvironment {
  readonly root: Container;
  readonly skyContainer: Container;
  readonly mountainContainer: Container;
  readonly groundContainer: Container;
  readonly backDecoContainer: Container;
  readonly foreDecoContainer: Container;
  readonly lightingContainer: Container;

  private assets: TinySwordsAssets;
  private dustMotes: { x: number; y: number; speed: number; phase: number }[] = [];
  private dustGraphics: Graphics;
  private lanternGlow: Graphics;
  private windowGlow: Graphics;

  constructor(assets: TinySwordsAssets) {
    this.assets = assets;
    this.root = new Container();
    this.root.label = 'TinySwordsEnvironmentRoot';

    this.skyContainer = new Container();
    this.mountainContainer = new Container();
    this.groundContainer = new Container();
    this.backDecoContainer = new Container();
    this.foreDecoContainer = new Container();
    this.lightingContainer = new Container();

    this.dustGraphics = new Graphics();
    this.lanternGlow = new Graphics();
    this.windowGlow = new Graphics();

    this.root.addChild(
      this.skyContainer,
      this.mountainContainer,
      this.groundContainer,
      this.backDecoContainer,
      this.foreDecoContainer,
      this.lightingContainer,
    );

    this.initDustParticles();
  }

  private initDustParticles() {
    this.dustMotes = [];
    for (let i = 0; i < 40; i++) {
      this.dustMotes.push({
        x: Math.random() * 640,
        y: Math.random() * 360,
        speed: 0.3 + Math.random() * 0.4,
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  /**
   * 构建环境中的静态地块、手绘大树、小路、栅栏与装饰物
   */
  buildScene(worldWidth: number, houseWorldX: number) {
    this.groundContainer.removeChildren();
    this.backDecoContainer.removeChildren();
    this.foreDecoContainer.removeChildren();
    this.lightingContainer.removeChildren();

    const { tree, tilemapFlat, deco01, deco02, deco03, deco07, deco08 } = this.assets;

    // 1. 无缝手绘草地地表（从 Tilemap_Flat 中切出 64×64 平铺草地块）
    const grassTileTex = extractSubTexture(tilemapFlat, 64, 64, 64, 64);
    const groundCols = Math.ceil(worldWidth / 64) + 8;
    const groundRows = 6; // 覆盖地平线以下全部区域
    for (let r = 0; r < groundRows; r++) {
      for (let c = -2; c < groundCols; c++) {
        const grassSpr = new Sprite(grassTileTex);
        grassSpr.position.set(c * 64, 184 + r * 64);
        this.groundContainer.addChild(grassSpr);
      }
    }

    // 2. 有机蜿蜒泥土小径（林间草地自然土径）
    const pathGraphics = new Graphics();
    for (let x = 0; x < worldWidth; x += 4) {
      const pathCenter = 270 + Math.sin(x * 0.015) * 16 + Math.cos(x * 0.035) * 6;
      const pathHalfW = 16 + Math.sin(x * 0.04) * 4;
      pathGraphics.ellipse(x, pathCenter, 6, pathHalfW);
    }
    pathGraphics.fill({ color: 0xd8ba82, alpha: 0.72 });
    this.groundContainer.addChild(pathGraphics);

    // 支路通向木屋正门
    const doorPathGraphics = new Graphics();
    for (let y = 205; y < 275; y += 4) {
      const branchX = houseWorldX + Math.sin(y * 0.04) * 6;
      doorPathGraphics.ellipse(branchX, y, 14, 4);
    }
    doorPathGraphics.fill({ color: 0xd8ba82, alpha: 0.65 });
    this.groundContainer.addChild(doorPathGraphics);

    // 3. 背景古树（Framing the forest clearing）
    const singleTreeTex = extractSubTexture(tree, 0, 0, 192, 192);
    const treePositions = [
      { x: houseWorldX - 340, y: 35, scale: 0.95 },
      { x: houseWorldX - 240, y: 42, scale: 0.85 },
      { x: houseWorldX - 150, y: 48, scale: 0.80 },
      { x: houseWorldX + 160, y: 45, scale: 0.80 },
      { x: houseWorldX + 250, y: 38, scale: 0.90 },
      { x: houseWorldX + 340, y: 40, scale: 0.95 },
    ];

    for (const pos of treePositions) {
      // 树木落影
      const shadowG = new Graphics();
      shadowG.ellipse(pos.x + 96 * pos.scale, pos.y + 192 * pos.scale - 12, 38 * pos.scale, 12 * pos.scale);
      shadowG.fill({ color: 0x0f2818, alpha: 0.35 });
      this.backDecoContainer.addChild(shadowG);

      const treeSpr = new Sprite(singleTreeTex);
      treeSpr.position.set(pos.x, pos.y);
      treeSpr.scale.set(pos.scale);
      this.backDecoContainer.addChild(treeSpr);
    }

    // 4. 木屋两侧与树下自然野趣（灌木、野花与林间小蘑菇）
    const bushLeft = new Sprite(deco01);
    bushLeft.scale.set(0.65);
    bushLeft.position.set(houseWorldX - 110, 195);

    const flowersLeft = new Sprite(deco03);
    flowersLeft.scale.set(0.5);
    flowersLeft.position.set(houseWorldX - 75, 210);

    const mushLeft1 = new Sprite(deco07);
    mushLeft1.scale.set(0.38);
    mushLeft1.position.set(houseWorldX - 145, 205);

    const mushLeft2 = new Sprite(deco08);
    mushLeft2.scale.set(0.38);
    mushLeft2.position.set(houseWorldX - 130, 208);

    const bushRight = new Sprite(deco02);
    bushRight.scale.set(0.6);
    bushRight.position.set(houseWorldX + 80, 198);

    const flowersRight = new Sprite(deco03);
    flowersRight.scale.set(0.5);
    flowersRight.position.set(houseWorldX + 105, 210);

    const mushRight = new Sprite(deco08);
    mushRight.scale.set(0.38);
    mushRight.position.set(houseWorldX + 140, 205);

    this.backDecoContainer.addChild(
      bushLeft,
      flowersLeft,
      mushLeft1,
      mushLeft2,
      bushRight,
      flowersRight,
      mushRight,
    );

    // 5. 前景步道边点缀自然野花与低矮灌木
    const fgBush1 = new Sprite(deco02);
    fgBush1.scale.set(0.55);
    fgBush1.position.set(houseWorldX - 120, 275);

    const fgFlowers1 = new Sprite(deco03);
    fgFlowers1.scale.set(0.5);
    fgFlowers1.position.set(houseWorldX - 55, 280);

    const fgFlowers2 = new Sprite(deco03);
    fgFlowers2.scale.set(0.5);
    fgFlowers2.position.set(houseWorldX + 75, 280);

    const fgBush2 = new Sprite(deco01);
    fgBush2.scale.set(0.65);
    fgBush2.position.set(houseWorldX + 160, 272);

    const fgMush = new Sprite(deco08);
    fgMush.scale.set(0.36);
    fgMush.position.set(houseWorldX + 135, 282);

    this.foreDecoContainer.addChild(fgBush1, fgFlowers1, fgFlowers2, fgBush2, fgMush);

    // 6. 前景右侧大树（增加电影级纵深感）
    const fgTreeSpr = new Sprite(singleTreeTex);
    fgTreeSpr.position.set(houseWorldX + 270, 75);
    fgTreeSpr.scale.set(1.15);
    const fgTreeShadow = new Graphics();
    fgTreeShadow.ellipse(houseWorldX + 270 + 96 * 1.15, 75 + 192 * 1.15 - 12, 48, 14);
    fgTreeShadow.fill({ color: 0x0f2818, alpha: 0.4 });
    this.foreDecoContainer.addChild(fgTreeShadow, fgTreeSpr);

    // 7. 温暖窗光与提灯光晕（对齐小木屋窗户与门廊）
    this.windowGlow.clear();
    this.windowGlow.circle(houseWorldX - 2, 168, 26);
    this.windowGlow.fill({ color: 0xffe082, alpha: 0.45 });
    this.lightingContainer.addChild(this.windowGlow);

    this.lanternGlow.clear();
    this.lanternGlow.circle(houseWorldX + 36, 188, 18);
    this.lanternGlow.fill({ color: 0xffb74d, alpha: 0.55 });
    this.lightingContainer.addChild(this.lanternGlow);

    this.lightingContainer.addChild(this.dustGraphics);
  }

  /**
   * 渲染吉卜力水彩天空与远山视差层
   */
  renderSkyAndMountains(viewportWidth: number, _viewportHeight: number, _cameraX: number, worldScale: number) {
    this.skyContainer.removeChildren();
    this.mountainContainer.removeChildren();

    const vWidth = Math.max(1280, Math.ceil(viewportWidth / worldScale) + 64);

    // 1. 水彩晨曦天空渐变 (天青蓝 #4a7c8f -> 薄雾绿 #b8dcd0 -> 暖晨白 #dcf2e3)
    const skyG = new Graphics();
    skyG.rect(0, 0, vWidth, 110);
    skyG.fill({ color: 0x4a7c8f });
    skyG.rect(0, 105, vWidth, 50);
    skyG.fill({ color: 0x76aab8 });
    skyG.rect(0, 150, vWidth, 25);
    skyG.fill({ color: 0xb8dcd0 });
    skyG.rect(0, 170, vWidth, 25);
    skyG.fill({ color: 0xdcf2e3 });
    this.skyContainer.addChild(skyG);

    // 2. 远山视差层 (Parallax ~0.2)
    const mountainG = new Graphics();
    mountainG.position.set(0, 0);

    // Far mountain ridge
    mountainG.poly([
      { x: -vWidth, y: 195 },
      { x: -vWidth * 0.5, y: 145 },
      { x: 0, y: 155 },
      { x: vWidth * 0.35, y: 138 },
      { x: vWidth * 0.7, y: 150 },
      { x: vWidth, y: 142 },
      { x: vWidth * 1.5, y: 152 },
      { x: vWidth * 2, y: 195 },
    ]);
    mountainG.fill({ color: 0x4f7c78, alpha: 0.55 });

    // Mid mountain ridge
    mountainG.poly([
      { x: -vWidth, y: 195 },
      { x: -vWidth * 0.4, y: 162 },
      { x: vWidth * 0.1, y: 158 },
      { x: vWidth * 0.45, y: 166 },
      { x: vWidth * 0.85, y: 156 },
      { x: vWidth * 1.3, y: 164 },
      { x: vWidth * 2, y: 195 },
    ]);
    mountainG.fill({ color: 0x3d6662, alpha: 0.78 });

    this.mountainContainer.addChild(mountainG);
  }

  /**
   * 逐帧更新光影呼吸与林间微光花粉粒子
   */
  update(timeMs: number, cameraX: number, worldScale: number) {
    // 窗光低频呼吸 (2.4s 周期)
    const breath = Math.sin(timeMs * 0.0026);
    this.windowGlow.alpha = 0.45 + breath * 0.12;
    this.lanternGlow.alpha = 0.55 + Math.sin(timeMs * 0.004) * 0.15;

    // 更新浮游花粉粒子
    this.dustGraphics.clear();
    for (const p of this.dustMotes) {
      p.y -= p.speed * 0.5;
      p.x += Math.sin(timeMs * 0.001 + p.phase) * 0.3;
      if (p.y < 30) p.y = 350;
      if (p.x < 0) p.x = 640;
      if (p.x > 640) p.x = 0;

      const alpha = 0.4 + Math.sin(timeMs * 0.002 + p.phase) * 0.3;
      this.dustGraphics.circle(p.x, p.y, 1.5);
      this.dustGraphics.fill({ color: 0xfff3d0, alpha });
    }

    // 缩放并设置相对相机的世界偏移
    const screenVirtualX = -cameraX;
    this.skyContainer.scale.set(worldScale);
    this.mountainContainer.scale.set(worldScale);
    this.groundContainer.scale.set(worldScale);
    this.backDecoContainer.scale.set(worldScale);
    this.foreDecoContainer.scale.set(worldScale);
    this.lightingContainer.scale.set(worldScale);

    this.mountainContainer.x = Math.round(((-cameraX * 0.18) % 640) * worldScale);
    this.groundContainer.x = Math.round(screenVirtualX * worldScale);
    this.backDecoContainer.x = Math.round(screenVirtualX * worldScale);
    this.foreDecoContainer.x = Math.round(screenVirtualX * worldScale);
    this.lightingContainer.x = Math.round(screenVirtualX * worldScale);
  }
}

/**
 * 创建 Tiny Swords 风格的高精手绘小木屋精灵
 */
export function createTinySwordsHouse(
  assets: TinySwordsAssets,
  houseId: CabinHouseId = 'cabin',
): Sprite {
  // 根据主题与房屋配置选用贴图
  const tex = houseId === 'villa' ? assets.houseBlue : assets.houseYellow;
  const spr = new Sprite(tex);
  spr.label = 'TinySwordsHouseSprite';
  // 保持手绘原木微像素比例：128×192
  spr.anchor.set(0.5, 1);
  return spr;
}
