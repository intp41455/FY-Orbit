import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import fixture from '../avatar/__fixtures__/w11.json';
import {
  DeskPetCompanion,
  DESK_PET_STORAGE_KEY,
  getPersonalityDialogue,
} from './DeskPetCompanion';

// 模拟 avatar API
const houseApiMock = {
  getHouseAvatar: vi.fn(),
  getMyAvatar: vi.fn(),
};

vi.mock('../../api/avatar', async () => {
  const actual = await vi.importActual<typeof import('../../api/avatar')>('../../api/avatar');
  return {
    ...actual,
    getHouseAvatar: (...args: unknown[]) => houseApiMock.getHouseAvatar(...args),
    getMyAvatar: (...args: unknown[]) => houseApiMock.getMyAvatar(...args),
  };
});

function createMockHouseAvatar() {
  return {
    fingerprint: 'USER_FP_12345678',
    layers: fixture.layers,
    matrix: fixture.matrix,
    width: 24,
    height: 48,
    palette: fixture.palette,
    char_keys: fixture.char_keys,
    char_palette: fixture.char_palette,
    labels: {
      mbti: 'INTJ',
      element: '木',
      mood: 'focused',
    },
  };
}

function notFoundError() {
  return Object.assign(new Error('not found'), { status: 404 });
}

function renderCompanion(initialRoute = '/workbench') {
  return render(
    <MemoryRouter initialEntries={[initialRoute]}>
      <DeskPetCompanion />
    </MemoryRouter>,
  );
}

describe('DeskPetCompanion · 工作台常驻伴侣系统', () => {
  beforeEach(() => {
    localStorage.clear();
    houseApiMock.getHouseAvatar.mockReset();
    houseApiMock.getMyAvatar.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('1. 路由感知与生命周期', () => {
    it('在 /workbench 页面正常呈现桌宠', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      renderCompanion('/workbench');

      expect(await screen.findByTestId('desk-pet-companion')).toBeInTheDocument();
      expect(screen.getByTestId('desk-pet-actor')).toBeInTheDocument();
      expect(screen.getByTestId('desk-pet-canvas')).toBeInTheDocument();
    });

    it('当路由是 /cabin（在我的小屋游戏中）时，桌宠自动隐匿，小人回到小屋中', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      renderCompanion('/cabin');

      // 组件在 /cabin 路由返回 null
      expect(screen.queryByTestId('desk-pet-companion')).not.toBeInTheDocument();
      expect(screen.queryByTestId('desk-pet-resting')).not.toBeInTheDocument();
    });
  });

  describe('2. 用户专属画像无缝联通', () => {
    it('读取真实专属小人画像：展现 MBTI、五行与专属微像素小人', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      const user = userEvent.setup();

      renderCompanion('/workbench');
      const actor = await screen.findByTestId('desk-pet-actor');

      // 点击小人展开气泡
      await user.click(actor);

      // 气泡展示
      expect(await screen.findByTestId('desk-pet-bubble')).toBeInTheDocument();
      expect(screen.getByText('木相 · INTJ')).toBeInTheDocument();

      // 台词体现 INTJ 与木相风格
      const text = screen.getByTestId('desk-pet-text');
      expect(text.textContent).toMatch(/任务脉络|变量|心流|推演/);
    });

    it('未生成画像时（404 空态）：显示温和萌宠，提示去画像测评解锁专属形象', async () => {
      houseApiMock.getHouseAvatar.mockRejectedValue(notFoundError());
      houseApiMock.getMyAvatar.mockRejectedValue(notFoundError());
      const user = userEvent.setup();

      renderCompanion('/workbench');
      const actor = await screen.findByTestId('desk-pet-actor');

      // 点击萌宠
      await user.click(actor);

      expect(await screen.findByTestId('desk-pet-bubble')).toBeInTheDocument();
      expect(screen.getByText('萌宠状态')).toBeInTheDocument();

      // 提示文字引导去测评
      const text = screen.getByTestId('desk-pet-text');
      expect(text.textContent).toContain('去画像测评解锁独一无二专属形象');

      // 包含快捷跳转测评入口
      expect(screen.getByTestId('pet-action-assessment')).toBeInTheDocument();
    });
  });

  describe('3. 快捷交互菜单与反馈', () => {
    it('支持「抚摸」交互并产生温情反馈与爱心动效', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      const user = userEvent.setup();

      renderCompanion('/workbench');
      const actor = await screen.findByTestId('desk-pet-actor');
      await user.click(actor);

      const patBtn = screen.getByTestId('pet-action-pat');
      await user.click(patBtn);

      // 触发特效
      expect(screen.getByTestId('desk-pet-fx')).toHaveTextContent('❤️');
      // 台词更新
      expect(screen.getByTestId('desk-pet-text').textContent).toContain('好温暖的心流共鸣');
    });

    it('支持「戳戳」交互并产生灵动反应', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      const user = userEvent.setup();

      renderCompanion('/workbench');
      const actor = await screen.findByTestId('desk-pet-actor');
      await user.click(actor);

      const pokeBtn = screen.getByTestId('pet-action-poke');
      await user.click(pokeBtn);

      // 触发特效
      expect(screen.getByTestId('desk-pet-fx')).toHaveTextContent('💡');
      // 台词更新
      expect(screen.getByTestId('desk-pet-text').textContent).toContain('被你轻轻戳中');
    });

    it('支持「查看画像卡片」展开/收起多维命理与性格档案', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      const user = userEvent.setup();

      renderCompanion('/workbench');
      const actor = await screen.findByTestId('desk-pet-actor');
      await user.click(actor);

      const cardBtn = screen.getByTestId('pet-action-profile');
      await user.click(cardBtn);

      // 档案卡片展示
      const card = await screen.findByTestId('desk-pet-profile-card');
      expect(card).toBeInTheDocument();
      expect(within(card).getByText('INTJ')).toBeInTheDocument();
      expect(within(card).getAllByText(/木相/).length).toBeGreaterThanOrEqual(1);
      expect(within(card).getByText('🎨 角色工坊换装')).toBeInTheDocument();

      // 再次点击收起
      await user.click(cardBtn);
      expect(screen.queryByTestId('desk-pet-profile-card')).not.toBeInTheDocument();
    });

    it('支持一键「🏡 进入我的小屋」直达 /cabin', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      const user = userEvent.setup();

      function AppWrapper() {
        const loc = useLocation();
        return (
          <>
            <div data-testid="current-path">{loc.pathname}</div>
            <DeskPetCompanion />
          </>
        );
      }

      render(
        <MemoryRouter initialEntries={['/workbench']}>
          <AppWrapper />
        </MemoryRouter>,
      );

      const actor = await screen.findByTestId('desk-pet-actor');
      await user.click(actor);

      const cabinBtn = screen.getByTestId('pet-action-cabin');
      await user.click(cabinBtn);

      // 验证路由跳至 /cabin，且伴侣自动隐匿
      await waitFor(() => {
        expect(screen.getByTestId('current-path')).toHaveTextContent('/cabin');
      });
      expect(screen.queryByTestId('desk-pet-companion')).not.toBeInTheDocument();
    });
  });

  describe('4. 用户自主开关控制与持久化', () => {
    it('点击「暂时休息」按钮可关闭桌宠，写入 localStorage，并显示轻量唤醒胶囊', async () => {
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      const user = userEvent.setup();

      renderCompanion('/workbench');
      const actor = await screen.findByTestId('desk-pet-actor');
      await user.click(actor);

      const toggleRestBtn = screen.getByTestId('desk-pet-toggle-rest');
      await user.click(toggleRestBtn);

      // 持久化为 false
      expect(localStorage.getItem(DESK_PET_STORAGE_KEY)).toBe('false');

      // 桌宠主体隐去，唤醒胶囊浮现
      expect(screen.queryByTestId('desk-pet-companion')).not.toBeInTheDocument();
      expect(await screen.findByTestId('desk-pet-resting')).toBeInTheDocument();
      expect(screen.getByTestId('desk-pet-wake-btn')).toBeInTheDocument();
    });

    it('点击唤醒胶囊可恢复桌宠，持久化更新为 true', async () => {
      localStorage.setItem(DESK_PET_STORAGE_KEY, 'false');
      houseApiMock.getHouseAvatar.mockResolvedValue(createMockHouseAvatar());
      const user = userEvent.setup();

      renderCompanion('/workbench');

      // 初始呈现休息胶囊
      const wakeBtn = await screen.findByTestId('desk-pet-wake-btn');
      await user.click(wakeBtn);

      // 持久化为 true
      expect(localStorage.getItem(DESK_PET_STORAGE_KEY)).toBe('true');

      // 桌宠重新恢复活动
      expect(await screen.findByTestId('desk-pet-companion')).toBeInTheDocument();
      expect(screen.queryByTestId('desk-pet-resting')).not.toBeInTheDocument();
    });
  });

  describe('5. 台词与性格推导逻辑单元测试', () => {
    it('根据不同人格类型与动作生成合理台词', () => {
      const ntLine = getPersonalityDialogue('ENTP', '火', 'sunny', 'idle', true);
      expect(ntLine).toContain('排除低效干扰');

      const nfLine = getPersonalityDialogue('INFJ', '水', 'calm', 'idle', true);
      expect(nfLine).toContain('静谧与沉浸心流');

      const sleepyLine = getPersonalityDialogue('INFP', '木', 'calm', 'sleepy', true);
      expect(sleepyLine).toContain('小憩');

      const noAvatarLine = getPersonalityDialogue('INFP', '木', 'calm', 'idle', false);
      expect(noAvatarLine).toContain('去画像测评解锁独一无二专属形象');
    });
  });
});
