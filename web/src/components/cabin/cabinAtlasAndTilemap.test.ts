import { describe, it, expect, beforeEach } from 'vitest';
import { Texture, type SpritesheetData } from 'pixi.js';
import {
  createCabinAtlas,
  registerTmjMap,
  loadTmjMap,
  clearAtlasCache,
  type TmjMap,
} from './cabinAtlasLoader';
import {
  buildTilemapFromTmj,
  computeCardinalMask,
  populateTilemapGrid,
} from './cabinTilemapRenderer';
import { CompositeTilemap } from '@pixi/tilemap';

describe('cabinAtlasLoader & cabinTilemapRenderer', () => {
  beforeEach(() => {
    clearAtlasCache();
  });

  const mockAtlasData: SpritesheetData = {
    frames: {
      tile_0000: {
        frame: { x: 0, y: 0, w: 32, h: 32 },
        sourceSize: { w: 32, h: 32 },
        spriteSourceSize: { x: 0, y: 0, w: 32, h: 32 },
      },
      tile_0001: {
        frame: { x: 32, y: 0, w: 32, h: 32 },
        sourceSize: { w: 32, h: 32 },
        spriteSourceSize: { x: 0, y: 0, w: 32, h: 32 },
      },
      cabin_wall: {
        frame: { x: 64, y: 0, w: 32, h: 32 },
        sourceSize: { w: 32, h: 32 },
        spriteSourceSize: { x: 0, y: 0, w: 32, h: 32 },
      },
    },
    meta: {
      scale: '1',
    },
  };

  it('creates loaded atlas and retrieves tile textures by GID and name', async () => {
    const baseTex = Texture.WHITE;
    const atlas = await createCabinAtlas('test_atlas', baseTex, mockAtlasData);

    expect(atlas.id).toBe('test_atlas');
    // GID 1 corresponds to tile_0000 (1-based index)
    const tex1 = atlas.getTileTexture(1);
    expect(tex1).not.toBeNull();

    // GID 2 corresponds to tile_0001
    const tex2 = atlas.getTileTexture(2);
    expect(tex2).not.toBeNull();

    // Semantic name
    const wallTex = atlas.getTileTexture('cabin_wall');
    expect(wallTex).not.toBeNull();

    // Non-existent GID returns null
    expect(atlas.getTileTexture(999)).toBeNull();
    expect(atlas.getTileTexture(0)).toBeNull();
  });

  it('computes 4-way cardinal mask for autotiling', () => {
    // 3x3 grid with primary center
    // 0 1 0
    // 1 1 1
    // 0 1 0
    const grid = [
      [0, 1, 0],
      [1, 1, 1],
      [0, 1, 0],
    ];
    // Center cell (1, 1) has neighbors North(1), East(2), South(4), West(8) -> 1+2+4+8 = 15
    const maskAll = computeCardinalMask(grid, 1, 1, 1);
    expect(maskAll).toBe(15);

    // Top-left (0, 0): has no North/West, East is 1 (2), South is 1 (4) -> mask = 6
    const maskTL = computeCardinalMask(grid, 0, 0, 1);
    expect(maskTL).toBe(6);
  });

  it('builds tilemap layers from TmjMap definition', async () => {
    const atlas = await createCabinAtlas('test_atlas_tmj', Texture.WHITE, mockAtlasData);

    const testTmj: TmjMap = {
      width: 4,
      height: 3,
      tilewidth: 32,
      tileheight: 32,
      orientation: 'orthogonal',
      renderorder: 'right-down',
      tilesets: [{ firstgid: 1, name: 'test', tilewidth: 32, tileheight: 32 }],
      layers: [
        {
          id: 1,
          name: 'Ground',
          type: 'tilelayer',
          visible: true,
          opacity: 1,
          width: 4,
          height: 3,
          x: 0,
          y: 0,
          data: [
            1, 1, 1, 1,
            1, 2, 2, 1,
            1, 1, 1, 1,
          ],
        },
        {
          id: 2,
          name: 'Structures',
          type: 'tilelayer',
          visible: true,
          opacity: 1,
          width: 4,
          height: 3,
          x: 0,
          y: 0,
          data: [
            0, 0, 0, 0,
            0, 1, 1, 0,
            0, 0, 0, 0,
          ],
        },
      ],
    };

    registerTmjMap('test_map', testTmj);
    const loadedMap = await loadTmjMap('test_map');
    expect(loadedMap.width).toBe(4);

    const rendered = buildTilemapFromTmj(loadedMap, atlas);
    expect(rendered.width).toBe(128); // 4 * 32
    expect(rendered.height).toBe(96);  // 3 * 32
    expect(rendered.groundTilemap).toBeInstanceOf(CompositeTilemap);
    expect(rendered.structuresTilemap).toBeInstanceOf(CompositeTilemap);

    rendered.destroy();
  });

  it('populates dynamic procedural grid using populateTilemapGrid', async () => {
    const atlas = await createCabinAtlas('test_dyn', Texture.WHITE, mockAtlasData);
    const tilemap = new CompositeTilemap();
    const grid = [
      ['tile_0000', 'tile_0001'],
      ['cabin_wall', 1],
    ];

    populateTilemapGrid(tilemap, grid, atlas, 32, 32);
    expect(tilemap).toBeInstanceOf(CompositeTilemap);
    tilemap.clear();
  });
});
