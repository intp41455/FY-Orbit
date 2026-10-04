// G5-3 · 视觉回归护栏（角色渲染快照）。
//
// 为什么不用 pixelmatch / PNG 比对库：环境无该二进制链，且像素画本身是
// 确定性的「矩阵 → 字节」映射，用稳定哈希即可零依赖拦截回归。
//
// 机制：
//  - 把「基准角色渲染」与「四帧待机派生」各自哈希成冻结基线（human-written 常量）。
//  - 任何有意/无意的像素改动（G5-2 描边重构、G5-1 生命感帧派生、后端 avatar_gen
//    矩阵漂移）都会让哈希失配 → 测试红。
//  - 禁 `vitest -u` 绕过：基线更新必须人工 review diff 后改常量（见协作拆分文档 §G5-3）。
//  - 自带「故意改坏一个像素」自检，证明护栏真的能拦。

import { describe, it, expect } from 'vitest';
import fixture from './__fixtures__/w11.json';
import {
  idleFrames,
  renderAvatar,
  type AvatarLayers,
} from './avatarPixels';

const layers = fixture.layers as unknown as AvatarLayers;
const palette = fixture.char_palette as Record<string, string>;

/** FNV-1a 32 位，确定性。输入可以是字节数组或字符串。 */
function fnv1a(input: Uint8ClampedArray | Uint8Array | string): string {
  let h = 0x811c9dc5;
  if (typeof input === 'string') {
    for (let i = 0; i < input.length; i++) {
      h ^= input.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
  } else {
    for (let i = 0; i < input.length; i++) {
      h ^= input[i];
      h = Math.imul(h, 0x01000193);
    }
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

function hashRgba(snap: { data: Uint8ClampedArray }): string {
  return fnv1a(snap.data);
}

function hashFrames(frames: { matrix: string[] }[]): string {
  return fnv1a(frames.map((f) => f.matrix.join('|')).join('\n'));
}

// 冻结基线：由人工写入后提交。禁止 `vitest -u` 自动更新绕过，更新须人审 diff。
// A2 升级为 24×48（2.5 头身）后的冻结基线。
const FROZEN_AVATAR_BASELINE = '55a9fbdf';
const FROZEN_IDLE_BASELINE = '2bf90e95';

describe('G5-3 · 视觉回归护栏（角色渲染快照）', () => {
  it('基准角色渲染与冻结快照一致（G5-2 描边 / 后端像素变更会被拦下）', () => {
    const snap = renderAvatar(layers, palette);
    expect(hashRgba(snap)).toBe(FROZEN_AVATAR_BASELINE);
  });

  it('四帧待机派生与冻结快照一致（G5-1 生命感变更会被拦下）', () => {
    const frames = idleFrames(layers);
    expect(hashFrames(frames)).toBe(FROZEN_IDLE_BASELINE);
  });

  it('故意改坏一个像素 → 哈希失配（护栏有效性自检）', () => {
    const snap = renderAvatar(layers, palette);
    const broken = new Uint8ClampedArray(snap.data);
    // 找到第一个不透明像素，翻转其 R 通道 = 改坏一个像素。
    let idx = -1;
    for (let i = 3; i < broken.length; i += 4) {
      if (broken[i] > 0) {
        idx = i - 3;
        break;
      }
    }
    expect(idx, '应存在不透明像素').toBeGreaterThanOrEqual(0);
    broken[idx] = (broken[idx] ^ 0xff) & 0xff;
    const h0 = hashRgba(snap);
    const h1 = fnv1a(broken);
    expect(h1, '改坏一个像素后哈希必须变化').not.toBe(h0);
  });
});
