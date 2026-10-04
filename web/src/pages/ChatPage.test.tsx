/**
 * P1 交互双件 · 任务一测试：ChatPage 提问侧边定位。
 * 覆盖：定位栏渲染（仅 user 消息、40 字摘要+时间）、点击触发平滑滚动与 2 秒高亮、
 * 视口内提问标记（IntersectionObserver stub）。
 * IntersectionObserver / scrollIntoView 在 jsdom 中不存在，这里打桩。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { ChatPage } from './ChatPage';
import type { ConversationSummary, Message } from '../api/types';

vi.mock('../api/conversations', () => ({
  conversationsApi: {
    list: vi.fn(), create: vi.fn(), get: vi.fn(), messages: vi.fn(), postMessage: vi.fn(),
  },
}));
import { conversationsApi } from '../api/conversations';

const MSGS: Message[] = [
  {
    id: 'm1', conversation_id: 'c1', role: 'user',
    content: '帮我总结这段经历中的关键转折', source: 'web',
    created_at: '2026-10-01T08:00:00Z', version: 1,
  },
  {
    id: 'm2', conversation_id: 'c1', role: 'assistant',
    content: '好的，我来帮你梳理。', source: 'model',
    created_at: '2026-10-01T08:00:05Z', version: 1,
  },
  {
    id: 'm3', conversation_id: 'c1', role: 'user',
    content: '再帮我看看情绪模式的重复规律，这条提问足够长，用来验证定位栏四十个字的摘要截断逻辑是否生效',
    source: 'web', created_at: '2026-10-01T08:01:00Z', version: 1,
  },
];

const CONV: ConversationSummary = {
  id: 'c1', title: '测试会话', domain: 'personal', mode: 'listen', version: 1,
  created_at: '2026-10-01T07:59:00Z', updated_at: '2026-10-01T08:01:00Z',
};

type IOEntry = { target: Element; isIntersecting: boolean };
type IOCb = (entries: IOEntry[]) => void;
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = [];
  cb: IOCb;
  observed: Element[] = [];
  constructor(cb: IOCb) {
    this.cb = cb;
    FakeIntersectionObserver.instances.push(this);
  }
  observe(el: Element) { this.observed.push(el); }
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
}

// flush：让 loadList → setActiveId → messages 的 promise 链在 act 内排空。
async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i++) await Promise.resolve();
  });
}

let scrollSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  FakeIntersectionObserver.instances = [];
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver);
  scrollSpy = vi.fn();
  (HTMLElement.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = scrollSpy;
  vi.mocked(conversationsApi.list).mockResolvedValue([CONV]);
  vi.mocked(conversationsApi.messages).mockResolvedValue(MSGS);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView;
});

describe('ChatPage 提问定位栏', () => {
  it('列出当前会话全部 user 消息（前 40 字摘要+时间），不含 assistant，气泡带稳定锚点', async () => {
    render(<ChatPage />);
    await flush();

    const items = screen.getAllByTestId('qnav-item');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('帮我总结这段经历中的关键转折');

    const summary3 = items[1].querySelector('.qnav-summary')?.textContent ?? '';
    const expected = MSGS[2].content.replace(/\s+/g, ' ').slice(0, 40);
    expect(summary3).toBe(`${expected}…`);

    // assistant 消息不出现在定位栏（出现在消息区不算）
    expect(screen.queryByText('好的，我来帮你梳理。', { selector: '.qnav-summary' })).toBeNull();

    // 消息元素带稳定 id 锚点
    expect(document.getElementById('msg-m1')).not.toBeNull();
    expect(document.getElementById('msg-m3')).not.toBeNull();
  });

  it('点击定位项平滑滚动到该消息并高亮，2 秒后取消高亮', async () => {
    render(<ChatPage />);
    await flush();

    // fake timers 与 userEvent 不兼容，这里用 fireEvent 同步派发点击
    const items = screen.getAllByTestId('qnav-item');
    fireEvent.click(items[1]);

    const target = document.getElementById('msg-m3') as HTMLElement;
    expect(target).not.toBeNull();
    expect(target.scrollIntoView).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: 'smooth' }),
    );
    expect(target.className).toContain('msg-flash');

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(target.className).not.toContain('msg-flash');
  });

  it('当前视口内的提问在定位栏显示「视口内」标记', async () => {
    render(<ChatPage />);
    await flush();

    // messages 加载后 effect 会重建 observer，取最后一个实例
    const io = FakeIntersectionObserver.instances.at(-1);
    expect(io).toBeDefined();
    expect(io?.observed).toHaveLength(3); // 观察全部消息气泡

    act(() => {
      io?.cb([
        { target: document.getElementById('msg-m1') as Element, isIntersecting: true },
        { target: document.getElementById('msg-m3') as Element, isIntersecting: false },
      ]);
    });

    const items = screen.getAllByTestId('qnav-item');
    expect(items[0].className).toContain('in-view');
    expect(items[0]).toHaveTextContent('视口内');
    expect(items[1].className).not.toContain('in-view');
  });
});
