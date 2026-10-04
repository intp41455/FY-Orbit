import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// jsdom 下 PixiJS/WebGL 不可用：按「纯逻辑 hook + 渲染层」拆分，渲染层整体 mock，
// 本文件只测工具条交互、持久化与台词逻辑（对应 cabinConfig.ts 纯逻辑层）。
vi.mock('../components/cabin/CabinStage', () => ({
  CabinStage: () => <canvas data-testid="cabin-canvas" aria-hidden="true" />,
}));

// 管家接线：本文件不测网络层，统一按「未配置模型」语义（status=false），
// 页面应诚实标注「预生成台词池」。分流行为见 cabinDialogueProvider.test.ts。
vi.mock('../api/butler', () => ({
  butlerApi: {
    status: () => Promise.resolve({ model_configured: false }),
    dialogue: () => Promise.reject(new Error('not configured')),
  },
}));

import { CabinPage } from './CabinPage';
import { cabinDialogue } from '../components/cabin/cabinDialogueProvider';
import {
  CABIN_STORAGE_KEY,
  DIALOGUE_LINES,
  DEFAULT_CABIN_CONFIG,
  getDialogueProvider,
  loadCabinConfig,
  pickDialogueLine,
  resolveDialogue,
  sanitizeCabinConfig,
  saveCabinConfig,
  setDialogueProvider,
} from '../components/cabin/cabinConfig';

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/cabin']}>
      <CabinPage />
    </MemoryRouter>,
  );
}

function storedConfig(): Record<string, unknown> {
  const raw = localStorage.getItem(CABIN_STORAGE_KEY);
  expect(raw).not.toBeNull();
  return JSON.parse(raw as string) as Record<string, unknown>;
}

beforeEach(() => {
  localStorage.clear();
  cabinDialogue.reset(); // 单例来源状态跨用例隔离
});

describe('CabinPage 工具条与持久化（fy.cabin.config.v1）', () => {
  it('挂载后渲染画布占位、标题与诚实台词来源标注', () => {
    renderPage();
    expect(screen.getByTestId('cabin-canvas')).toBeInTheDocument();
    expect(screen.getByText('🏠 我的小屋')).toBeInTheDocument();
    // 管家接线（cabinDialogueProvider）：status 探测前/未配置时来源标注为预生成台词池
    expect(screen.getByTestId('cabin-dialogue-source')).toHaveTextContent('预生成台词池');
    expect(screen.getByText(/绝不伪装模型生成/)).toBeInTheDocument();
    expect(screen.getByText('虚拟演绎')).toBeInTheDocument();
  });

  it('房屋模板切换：实时高亮 + 写入 localStorage', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('house-snowcave'));
    expect(screen.getByTestId('house-snowcave').className).toContain('active');
    expect(storedConfig().house).toBe('snowcave');
  });

  it('背景切换：写入 localStorage', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('bg-planet'));
    expect(storedConfig().background).toBe('planet');
  });

  it('宠物颜色与性格：写入 localStorage', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('pet-color-sakura'));
    fireEvent.change(screen.getByTestId('pet-personality'), { target: { value: 'cool' } });
    const cfg = storedConfig();
    expect(cfg.petColor).toBe('sakura');
    expect(cfg.petPersonality).toBe('cool');
  });

  it('小人名字与性格：实时生效 + 持久化', () => {
    renderPage();
    fireEvent.change(screen.getByTestId('person-name'), { target: { value: '阿橙' } });
    fireEvent.change(screen.getByTestId('person-personality'), { target: { value: 'melancholy' } });
    const cfg = storedConfig();
    expect(cfg.personName).toBe('阿橙');
    expect(cfg.personPersonality).toBe('melancholy');
    expect((screen.getByTestId('person-name') as HTMLInputElement).value).toBe('阿橙');
  });

  it('损坏的存档：回退默认值，不抛错', () => {
    localStorage.setItem(CABIN_STORAGE_KEY, '{broken json');
    renderPage();
    // 默认房屋是 cabin（小木屋），页面应正常挂载且配置回落默认
    expect(loadCabinConfig()).toEqual(DEFAULT_CABIN_CONFIG);
    expect(screen.getByTestId('house-cabin').className).toContain('active');
  });

  it('非法字段值：逐字段回退白名单默认', () => {
    localStorage.setItem(
      CABIN_STORAGE_KEY,
      JSON.stringify({ house: '金字塔', background: 42, petColor: 'rainbow', personName: '   ' }),
    );
    expect(loadCabinConfig()).toEqual(DEFAULT_CABIN_CONFIG);
  });
});

describe('cabinConfig 纯逻辑：台词挑选与模型接口位', () => {
  it('pickDialogueLine 随机源确定：rng=0 取首条，rng≈1 取末条', () => {
    const pool = DIALOGUE_LINES.lively.pet;
    expect(pickDialogueLine('pet', 'lively', () => 0)).toBe(pool[0]);
    expect(pickDialogueLine('pet', 'lively', () => 0.999)).toBe(pool[pool.length - 1]);
  });

  it('四种性格的台词池互不相同且选出的台词属于对应池', () => {
    const ids = ['lively', 'cool', 'melancholy', 'chatty'] as const;
    for (const id of ids) {
      for (const speaker of ['person', 'pet'] as const) {
        const line = pickDialogueLine(speaker, id, () => 0.5);
        expect(DIALOGUE_LINES[id][speaker]).toContain(line);
      }
    }
    expect(DIALOGUE_LINES.lively.person).not.toEqual(DIALOGUE_LINES.cool.person);
    expect(DIALOGUE_LINES.lively.pet).not.toEqual(DIALOGUE_LINES.chatty.pet);
  });

  it('默认 provider 走预生成台词池；注入模型 provider 后 resolveDialogue 走新通道', async () => {
    const original = getDialogueProvider();
    const line = await Promise.resolve(resolveDialogue('person', 'chatty'));
    expect(DIALOGUE_LINES.chatty.person).toContain(line);

    // 模型接口位注入（未来接 /api/chat 或本地 inference 时同款写法）
    setDialogueProvider(() => Promise.resolve('模型生成台词'));
    await expect(resolveDialogue('pet', 'lively')).resolves.toBe('模型生成台词');

    setDialogueProvider(original); // 还原默认池，避免污染其他用例
  });
});

describe('cabinConfig 纯逻辑：sanitize / save / load', () => {
  it('sanitize 透传合法字段并裁剪超长名字', () => {
    const cfg = sanitizeCabinConfig({
      house: 'castle',
      background: 'stream',
      petColor: 'mint',
      petPersonality: 'chatty',
      personPersonality: 'cool',
      personName: 'x'.repeat(30),
    });
    expect(cfg).toEqual({
      house: 'castle',
      background: 'stream',
      petColor: 'mint',
      petPersonality: 'chatty',
      personPersonality: 'cool',
      personName: 'x'.repeat(16),
      timeOfDay: 'day',
    });
  });

  it('save → load 往返一致（结构即未来后端 cabin_config JSON）', () => {
    const cfg = { ...DEFAULT_CABIN_CONFIG, house: 'bunker' as const, personName: '小屋主' };
    saveCabinConfig(cfg);
    expect(loadCabinConfig()).toEqual(cfg);
  });
});
