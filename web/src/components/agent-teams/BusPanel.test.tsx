/**
 * W7 · 团队房间会话面板单测。
 *
 * 覆盖验收清单里前端那条链路：本人发言 → 点名成员 → 看到真实（或诚实 system）
 * 回复；handoff 在连线徽标上有计数；越权与失败原样暴露、不包装成成功。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/bus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/bus')>();
  return {
    ...actual,
    busApi: {
      list: vi.fn(),
      send: vi.fn(),
      handoffs: vi.fn(),
      context: vi.fn(),
      addContext: vi.fn(),
      streamUrl: vi.fn(),
    },
    subscribeBus: vi.fn(),
  };
});

import { busApi, subscribeBus, type BusMessage } from '../../api/bus';
import { BusPanel } from './BusPanel';

const bus = busApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const subscribe = subscribeBus as unknown as ReturnType<typeof vi.fn>;

const MEMBERS = [
  { role: 'coordinator', title: '协调官' },
  { role: 'implementer', title: '编码专家' },
];

/** subscribeBus 捕获：测试里手动推一条实时消息。 */
let emit: ((m: BusMessage) => void) | null = null;
let unsubscribeCalled = 0;

function msg(id: number, over: Partial<BusMessage> = {}): BusMessage {
  return {
    id,
    room: 'team-1',
    from_identity: 'owner:o1',
    kind: 'text',
    content: '',
    refs: [],
    at: '2026-01-01T00:00:00Z',
    mention: null,
    ...over,
  };
}

function okList(items: BusMessage[]) {
  bus.list.mockResolvedValue({ room: 'team-1', kind: 'team', items, count: items.length, next_after_id: items.length });
}
function okEmpty() {
  bus.handoffs.mockResolvedValue({ room: 'team-1', kind: 'team', edges: {}, total: 0 });
  bus.context.mockResolvedValue({ room: 'team-1', kind: 'team', items: [], count: 0 });
}

beforeEach(() => {
  emit = null;
  unsubscribeCalled = 0;
  bus.list.mockReset();
  bus.send.mockReset();
  bus.handoffs.mockReset();
  bus.context.mockReset();
  bus.addContext.mockReset();
  subscribe.mockReset();
  subscribe.mockImplementation((_room: string, cb: (m: BusMessage) => void) => {
    emit = cb;
    return () => {
      unsubscribeCalled += 1;
    };
  });
  okEmpty();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('BusPanel', () => {
  it('渲染真实消息流：谁发言、@了谁、系统消息都可见', async () => {
    okList([
      msg(1, { content: '请把这个函数实现一下', mention: 'agent:implementer' }),
      msg(2, { from_identity: 'agent:implementer', content: '收到，我来实现这个函数。' }),
      msg(3, { from_identity: 'system', kind: 'system', content: '模型未配置，无法回应。' }),
    ]);
    const { container } = render(<BusPanel room="team-1" members={MEMBERS} />);

    await waitFor(() => expect(screen.getByText('收到，我来实现这个函数。')).toBeInTheDocument());
    const who = Array.from(container.querySelectorAll('.bus-who')).map((n) => n.textContent);
    expect(who).toEqual(['我', '编码专家', '系统']);
    expect(screen.getByText('@编码专家')).toBeInTheDocument();
    expect(screen.getByText('模型未配置，无法回应。')).toBeInTheDocument();
    expect(bus.list).toHaveBeenCalledWith('team-1', 0);
  });

  it('以本人身份发言并点名成员：返回消息入列，提示已点名', async () => {
    okList([]);
    const sent = msg(1, { content: '@编码专家 请把这个函数实现一下', mention: 'agent:implementer' });
    bus.send.mockResolvedValue({
      message: sent,
      triggered: ['implementer'],
      scheduled: true,
      auto_reply_enabled: true,
    });
    render(<BusPanel room="team-1" members={MEMBERS} />);
    await waitFor(() => expect(bus.list).toHaveBeenCalled());

    await userEvent.type(screen.getByLabelText('以本人身份发言'), '@编码专家 请把这个函数实现一下');
    await userEvent.selectOptions(screen.getByLabelText(/点名成员/), 'agent:implementer');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() =>
      expect(screen.getByText(/已点名 implementer/)).toBeInTheDocument(),
    );
    expect(bus.send).toHaveBeenCalledTimes(1);
    const body = bus.send.mock.calls[0][1] as Record<string, unknown>;
    expect(Object.keys(body)).not.toContain('from_identity');
    expect(body.mention).toBe('agent:implementer');
    expect(screen.getByText('@编码专家 请把这个函数实现一下')).toBeInTheDocument();
  });

  it('handoff 交接在连线徽标上计数（后端 handoffs 端点）', async () => {
    okList([]);
    bus.handoffs.mockResolvedValue({
      room: 'team-1',
      kind: 'team',
      edges: { 'o1>reviewer': 2, 'coordinator>implementer': 1 },
      total: 3,
    });
    render(<BusPanel room="team-1" members={MEMBERS} />);

    await waitFor(() => expect(screen.getByText('o1 → reviewer')).toBeInTheDocument());
    expect(screen.getByText('×2')).toBeInTheDocument();
    expect(screen.getByText('coordinator → implementer')).toBeInTheDocument();
    expect(screen.getByText('×1')).toBeInTheDocument();
  });

  it('越权房间：错误原样暴露为 alert，不假装空房间', async () => {
    bus.list.mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        status: 403,
        body: { code: 'bus_room_forbidden', message: '无权访问' },
      }),
    );
    render(<BusPanel room="team-other" members={MEMBERS} />);

    const alert = await screen.findByRole('alert');
    // 后端给了 message 就应原样透出（中文），不再被改写成英文通用串 ——
    // 这条用例守的是「错误不被吞掉」，不是某个英文字面量。
    expect(alert.textContent).toContain('无权访问');
    expect(screen.queryByText('这个房间还没有消息。')).not.toBeInTheDocument();
  });

  it('模型未配置时的 system 消息原文显示，不包装成成员回答', async () => {
    okList([msg(4, { from_identity: 'system', kind: 'system', content: '模型未配置，无法回应。请先在设置里配置模型凭据；此处不生成任何冒充回答的内容。' })]);
    render(<BusPanel room="team-1" members={MEMBERS} />);

    await waitFor(() =>
      expect(screen.getByText(/模型未配置，无法回应/)).toBeInTheDocument(),
    );
    // 关键：没有任何一条消息是以 agent: 身份发出的伪造回答
    expect(screen.queryByText(/收到，我来实现/)).not.toBeInTheDocument();
  });

  it('订阅回调推入的实时消息会追加到消息流（按 id 去重）', async () => {
    okList([msg(1, { content: '第一条' })]);
    const { container } = render(<BusPanel room="team-1" members={MEMBERS} />);
    await waitFor(() => expect(screen.getByText('第一条')).toBeInTheDocument());

    emit?.(msg(2, { from_identity: 'agent:implementer', content: '后台生成的回复' }));
    emit?.(msg(2, { from_identity: 'agent:implementer', content: '后台生成的回复' })); // 重复 id：应被去重

    await waitFor(() => expect(screen.getByText('后台生成的回复')).toBeInTheDocument());
    expect(container.querySelectorAll('.bus-msg')).toHaveLength(2);
    expect(subscribe).toHaveBeenCalledTimes(1);
  });
});
