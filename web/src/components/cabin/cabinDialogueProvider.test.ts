// 管家台词通道接线测试：provider 状态分流（configured→模型 / 否则→池）、失败回退。
// 通过 createCabinDialogueController 注入 api 仿件，不动全局 fetch；每次用例后
// reset() 恢复默认池 provider，避免污染其他文件（vitest 每文件独立模块图，双保险）。
import { afterEach, describe, expect, it } from 'vitest';
import {
  createCabinDialogueController,
  type ButlerApiLike,
} from './cabinDialogueProvider';
import {
  DIALOGUE_LINES,
  getDialogueProvider,
  resolveDialogue,
  setDialogueProvider,
} from './cabinConfig';

afterEach(() => {
  // 恢复默认池 provider（等价于 cabinConfig 的出厂默认）
  setDialogueProvider((speaker, personality) => {
    const pool = DIALOGUE_LINES[personality][speaker];
    return pool[0];
  });
});

function fakeApi(overrides: {
  status?: () => Promise<{ model_configured: boolean }>;
  dialogue?: (body: { speaker: string; personality: string; context?: Record<string, unknown> }) => Promise<{ line: string }>;
}): { api: ButlerApiLike; dialogueBodies: Array<{ speaker: string; personality: string; context?: Record<string, unknown> }> } {
  const dialogueBodies: Array<{ speaker: string; personality: string; context?: Record<string, unknown> }> = [];
  const api: ButlerApiLike = {
    status: () =>
      overrides.status
        ? overrides.status()
        : Promise.resolve({ model_configured: false }),
    dialogue: (body) => {
      dialogueBodies.push(body);
      return overrides.dialogue
        ? overrides.dialogue(body)
        : Promise.reject(new Error('not configured'));
    },
  };
  return { api, dialogueBodies };
}

describe('cabinDialogueProvider：provider 状态分流与诚实回退', () => {
  it('未配置模型（status.model_configured=false）：来源=池，resolveDialogue 走预生成台词池', async () => {
    const { api } = fakeApi({});
    const ctl = createCabinDialogueController({ api });
    expect(ctl.getSource()).toBeNull(); // 探测前无结论

    await expect(ctl.install()).resolves.toBe('pool');
    expect(ctl.getSource()).toBe('pool');

    const line = await Promise.resolve(resolveDialogue('person', 'cool'));
    expect(DIALOGUE_LINES.cool.person).toContain(line);
  });

  it('status 探测失败（网络/未登录）：与未配置同语义，诚实回退池', async () => {
    const { api } = fakeApi({ status: () => Promise.reject(new Error('network down')) });
    const ctl = createCabinDialogueController({ api });

    await expect(ctl.install()).resolves.toBe('pool');
    expect(ctl.getSource()).toBe('pool');
    const line = await Promise.resolve(resolveDialogue('pet', 'lively'));
    expect(DIALOGUE_LINES.lively.pet).toContain(line);
  });

  it('已配置模型：install 后走管家 dialogue，来源=模型，context 透传', async () => {
    const { api, dialogueBodies } = fakeApi({
      status: () => Promise.resolve({ model_configured: true }),
      dialogue: (body) => {
        expect(body.speaker).toBe('person');
        expect(body.personality).toBe('chatty');
        return Promise.resolve({ line: '（模型）晚上好，主人。' });
      },
    });
    const ctl = createCabinDialogueController({ api });

    await expect(
      ctl.install({ context: () => ({ user_name: '小寻' }) }),
    ).resolves.toBe('model');
    expect(ctl.getSource()).toBe('model');

    await expect(resolveDialogue('person', 'chatty')).resolves.toBe('（模型）晚上好，主人。');
    expect(dialogueBodies[0]?.context).toEqual({ user_name: '小寻' });
    // getDialogueProvider 暴露的是接线安装的模型 provider（setDialogueProvider 生效）
    expect(getDialogueProvider()).not.toBeNull();
  });

  it('已配置但 dialogue 调用失败（503/429/网络）：回退池并如实把来源翻回「预生成台词池」', async () => {
    let shouldFail = true;
    const { api } = fakeApi({
      status: () => Promise.resolve({ model_configured: true }),
      dialogue: () =>
        shouldFail ? Promise.reject(new Error('503 model_not_configured')) : Promise.resolve({ line: '（模型）回来啦。' }),
    });
    const ctl = createCabinDialogueController({ api });
    await ctl.install();

    // 第一次调用失败 → 池台词 + 来源翻回 pool
    const failedLine = await Promise.resolve(resolveDialogue('pet', 'melancholy'));
    expect(DIALOGUE_LINES.melancholy.pet).toContain(failedLine);
    expect(ctl.getSource()).toBe('pool');

    // 模型恢复后（fail-open 不存在：仍走模型通道）成功 → 来源翻回 model
    shouldFail = false;
    await expect(resolveDialogue('pet', 'melancholy')).resolves.toBe('（模型）回来啦。');
    expect(ctl.getSource()).toBe('model');
  });

  it('reset()：恢复默认池 provider 并清空来源状态', async () => {
    const { api } = fakeApi({ status: () => Promise.resolve({ model_configured: true }) });
    const ctl = createCabinDialogueController({ api });
    await ctl.install();
    expect(ctl.getSource()).toBe('model');

    ctl.reset();
    expect(ctl.getSource()).toBeNull();
    const line = await Promise.resolve(resolveDialogue('person', 'melancholy'));
    expect(DIALOGUE_LINES.melancholy.person).toContain(line);
  });
});
