import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * W1 · CabinPage 室内接线测试
 *
 * jsdom 下 PixiJS/WebGL 不可用：室外/室内两个渲染层都整体 mock，
 * 本文件只测**页面接线与状态机**：进屋/出屋、布置模式、家具增删改、
 * 持久化往返、以及「后端失败时诚实报错」这条底线。
 */
vi.mock('../components/cabin/CabinStage', () => ({
  CabinStage: () => <canvas data-testid="cabin-canvas" aria-hidden="true" />,
}));

// 室内渲染层 mock：把回调暴露成可点击按钮，模拟画布上的点击/拖拽。
vi.mock('../components/cabin/interior/InteriorStage', () => ({
  InteriorStage: (props: {
    layout: { items: { id: string; furnitureId: string }[] };
    editMode: boolean;
    callbacks?: {
      onFurnitureTap?: (item: unknown) => void;
      onFloorTap?: (gx: number, gy: number) => void;
      onExit?: () => void;
      onItemMoved?: (id: string, gx: number, gy: number) => void;
      onItemSelected?: (id: string | null) => void;
    };
  }) => {
    const cb = props.callbacks ?? {};
    return (
      <div data-testid="interior-stage" data-edit={props.editMode ? 'on' : 'off'}>
        {props.layout.items.map((it) => (
          <button
            key={it.id}
            type="button"
            data-testid={`stage-item-${it.furnitureId}`}
            onClick={() => cb.onFurnitureTap?.(it)}
          >
            {it.furnitureId}
          </button>
        ))}
        <button type="button" data-testid="stage-floor" onClick={() => cb.onFloorTap?.(4, 5)}>
          floor
        </button>
        <button type="button" data-testid="stage-door" onClick={() => cb.onExit?.()}>
          door
        </button>
        <button
          type="button"
          data-testid="stage-drag"
          onClick={() => cb.onItemMoved?.(props.layout.items[0]?.id ?? '', 7, 6)}
        >
          drag
        </button>
      </div>
    );
  },
}));

vi.mock('../api/butler', () => ({
  butlerApi: {
    status: () => Promise.resolve({ model_configured: false }),
    dialogue: () => Promise.reject(new Error('not configured')),
  },
}));

import { CabinPage } from './CabinPage';
import { cabinDialogue } from '../components/cabin/cabinDialogueProvider';
import { defaultLayout, interiorStorageKey, MAX_ITEMS } from '../components/cabin/interior/interiorLayout';

/** 后端通道可控 mock：默认「无存档」，各用例按需覆盖。 */
const backend = {
  get: vi.fn(),
  put: vi.fn(),
};

vi.mock('../components/cabin/interior/cabinInteriorApi', () => ({
  loadInteriorWithCache: (houseId: string) => backend.get(houseId),
  saveInterior: (houseId: string, layout: unknown, expected: number) =>
    backend.put(houseId, layout, expected),
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/cabin']}>
      <CabinPage />
    </MemoryRouter>,
  );
}

/** 默认：后端无存档（defaulted）。 */
function useEmptyBackend() {
  backend.get.mockResolvedValue({ layout: null, fromBackend: false });
  backend.put.mockResolvedValue({ house_id: 'cabin', layout: {}, version: 1, defaulted: false });
}

/** 后端已有一份存档。 */
function useStoredBackend(layout: unknown) {
  backend.get.mockResolvedValue({ layout, fromBackend: true });
  backend.put.mockResolvedValue({ house_id: 'cabin', layout, version: 5, defaulted: false });
}

beforeEach(() => {
  localStorage.clear();
  cabinDialogue.reset();
  vi.clearAllMocks();
  useEmptyBackend();
});

describe('CabinPage 室内：进出屋', () => {
  it('屋外默认显示室外画布与「进屋布置」按钮', () => {
    renderPage();
    expect(screen.getByTestId('cabin-canvas')).toBeInTheDocument();
    expect(screen.getByTestId('cabin-enter-indoor')).toBeInTheDocument();
    expect(screen.queryByTestId('interior-stage')).toBeNull();
  });

  it('点「进屋」切到室内画布，室外画布卸载', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    expect(await screen.findByTestId('interior-stage')).toBeInTheDocument();
    expect(screen.queryByTestId('cabin-canvas')).toBeNull();
  });

  it('点门（画布回调）回室外', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('stage-door'));
    expect(await screen.findByTestId('cabin-canvas')).toBeInTheDocument();
  });

  it('「出门回院子」按钮同样能回室外', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-exit-indoor'));
    expect(await screen.findByTestId('cabin-canvas')).toBeInTheDocument();
  });
});

describe('CabinPage 室内：布置模式', () => {
  it('默认非编辑态；点「布置」后面板出现且画布进入编辑态', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    expect(screen.queryByTestId('interior-decorate')).toBeNull();

    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    expect(await screen.findByTestId('interior-decorate')).toBeInTheDocument();
    expect(screen.getByTestId('interior-stage').dataset.edit).toBe('on');
  });

  it('「完成」退出布置，面板消失', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    await screen.findByTestId('interior-decorate');
    fireEvent.click(screen.getByTestId('interior-exit-edit'));
    expect(screen.queryByTestId('interior-decorate')).toBeNull();
  });

  it('家具栏含更衣镜（W11 角色工坊入口）', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    expect(await screen.findByTestId('furniture-mirror')).toBeInTheDocument();
  });

  it('点击家具 → 落位 + 计数增加 + 标记未保存', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    const before = (await screen.findByTestId('interior-item-count')).textContent;

    fireEvent.click(screen.getByTestId('furniture-plant'));
    await waitFor(() => {
      expect(screen.getByTestId('interior-item-count').textContent).not.toBe(before);
    });
    // 未保存时保存按钮可用
    expect(screen.getByTestId('interior-save')).not.toBeDisabled();
  });

  it('拖拽家具（画布回调）更新布局并标脏', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    await screen.findByTestId('interior-decorate');

    fireEvent.click(screen.getByTestId('stage-drag'));
    await waitFor(() => {
      expect(screen.getByTestId('interior-save')).not.toBeDisabled();
    });
  });

  it('选中家具后可翻转/换配色/调层/删除', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    await screen.findByTestId('interior-decorate');

    // 放置一件并选中（handleAdd 会自动选中）
    fireEvent.click(screen.getByTestId('furniture-plant'));
    const selection = await screen.findByTestId('interior-selection');
    expect(selection).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('interior-flip'));
    fireEvent.click(screen.getByTestId('interior-colorway'));
    fireEvent.click(screen.getByTestId('interior-layer-up'));
    fireEvent.click(screen.getByTestId('interior-layer-down'));
    expect(screen.getByTestId('interior-selection')).toBeInTheDocument();

    const beforeRemove = screen.getByTestId('interior-item-count').textContent;
    fireEvent.click(screen.getByTestId('interior-remove'));
    await waitFor(() => {
      expect(screen.getByTestId('interior-item-count').textContent).not.toBe(beforeRemove);
    });
  });

  it('骰子按钮随机加家具', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    const before = (await screen.findByTestId('interior-item-count')).textContent;
    fireEvent.click(screen.getByTestId('interior-dice'));
    await waitFor(() => {
      expect(screen.getByTestId('interior-item-count').textContent).not.toBe(before);
    });
  });

  it('网格吸附开关可切换', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    const toggle = (await screen.findByTestId('interior-snap-toggle')) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    fireEvent.click(toggle);
    await waitFor(() => {
      expect((screen.getByTestId('interior-snap-toggle') as HTMLInputElement).checked).toBe(false);
    });
  });
});

describe('CabinPage 室内：家具交互（非布置态）', () => {
  it('点床 → 睡觉演出（由 sleepToken 通道驱动，此处断言不崩且仍在室内）', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(await screen.findByTestId('stage-item-bed'));
    // 睡觉演出在场景层；页面不因此报错，仍留在室内
    expect(screen.getByTestId('interior-stage')).toBeInTheDocument();
  });

  it('点书架 → 台词气泡（sayToken）；点无交互家具不产生任何演出', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(await screen.findByTestId('stage-item-bookshelf'));
    expect(screen.getByTestId('interior-stage')).toBeInTheDocument();

    // table 是 interact='none'：诚实不演出，不抛错
    fireEvent.click(screen.getByTestId('stage-item-table'));
    expect(screen.getByTestId('interior-stage')).toBeInTheDocument();
  });

  it('点更衣镜 → 诚实提示「角色工坊施工中」（W11 未落地，不假装能换装）', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(await screen.findByTestId('stage-item-mirror'));
    expect(screen.getByTestId('interior-stage')).toBeInTheDocument();
    expect(screen.getByText(/角色工坊/)).toBeInTheDocument();
  });

  it('点地板 → 小人移动（personToken 通道）', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('stage-floor'));
    expect(screen.getByTestId('interior-stage')).toBeInTheDocument();
  });
});

describe('CabinPage 室内：持久化（后端为准 + 本地双写）', () => {
  it('后端无存档 → 用默认布置并回写一次（expected_version=0）', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    await waitFor(() => {
      expect(backend.put).toHaveBeenCalled();
    });
    expect(backend.put.mock.calls[0]![2]).toBe(0);
  });

  it('后端已有存档 → 读回并写入 localStorage', async () => {
    const stored = defaultLayout('castle');
    backend.get.mockResolvedValue({ layout: stored, fromBackend: true });
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    await waitFor(() => {
      expect(localStorage.getItem(interiorStorageKey('castle'))).not.toBeNull();
    });
    // 已有存档就不该再自动 PUT
    expect(backend.put).not.toHaveBeenCalled();
  });

  it('点「保存布置」→ 调后端并显示已保存版本', async () => {
    useStoredBackend(defaultLayout('cabin'));
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    await screen.findByTestId('interior-decorate');
    fireEvent.click(screen.getByTestId('furniture-plant'));
    fireEvent.click(await screen.findByTestId('interior-save'));
    await waitFor(() => {
      expect(backend.put).toHaveBeenCalled();
    });
    expect(await screen.findByTestId('cabin-save-state')).toHaveTextContent(/云端/);
  });

  it('后端保存失败 → 明确报错并保持未保存状态（不假装成功）', async () => {
    useStoredBackend(defaultLayout('cabin'));
    backend.put.mockRejectedValue(new Error('500 内部错误'));
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    await screen.findByTestId('interior-decorate');
    fireEvent.click(screen.getByTestId('furniture-plant'));
    fireEvent.click(await screen.findByTestId('interior-save'));
    const state = await screen.findByTestId('cabin-save-state');
    expect(state).toHaveTextContent(/云端保存失败/);
    expect(state).toHaveTextContent(/500/);
    // 仍可重试：保存按钮保持可用
    expect(screen.getByTestId('interior-save')).not.toBeDisabled();
  });

  it('云端读取失败 → 显示明确错误并回退默认布置（诚实降级）', async () => {
    backend.get.mockRejectedValue(new Error('网络不可达'));
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    const err = await screen.findByTestId('cabin-load-error');
    expect(err).toHaveTextContent(/云端布置读取失败/);
    expect(err).toHaveTextContent(/网络不可达/);
    // 仍能正常布置（工具条可用）
    expect(screen.getByTestId('cabin-toggle-edit')).toBeInTheDocument();
  });

  it('「恢复默认」重置为该模板默认布置', async () => {
    useStoredBackend({ houseId: 'cabin', items: [], version: 3 });
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    await screen.findByTestId('interior-decorate');
    fireEvent.click(screen.getByTestId('interior-reset'));
    await waitFor(() => {
      const text = screen.getByTestId('interior-item-count').textContent ?? '';
      const n = Number(text.match(/\d+/)?.[0] ?? '-1');
      expect(n).toBe(defaultLayout('cabin').items.length);
    });
  });
});

describe('CabinPage 室内：切换房屋模板 → 切到对应布置', () => {
  it('换模板后重新拉取该模板的布置（按 house_id 分套）', async () => {
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    backend.get.mockClear();
    fireEvent.click(screen.getByTestId('house-snowcave'));
    await waitFor(() => {
      expect(backend.get).toHaveBeenCalledWith('snowcave');
    });
  });

  it('家具数量上限后拒绝继续添加（诚实提示，不静默丢弃）', async () => {
    useStoredBackend({
      houseId: 'cabin',
      version: 4,
      items: Array.from({ length: MAX_ITEMS }, (_, i) => ({
        id: `f${i}`,
        furnitureId: 'chair',
        x: 1,
        y: 1,
        flipped: false,
        colorway: 0,
        z: 0,
      })),
    });
    renderPage();
    fireEvent.click(screen.getByTestId('cabin-enter-indoor'));
    await screen.findByTestId('interior-stage');
    fireEvent.click(screen.getByTestId('cabin-toggle-edit'));
    await screen.findByTestId('interior-decorate');
    fireEvent.click(screen.getByTestId('furniture-plant'));
    const state = await screen.findByTestId('cabin-save-state');
    expect(state).toHaveTextContent(/上限/);
  });
});
