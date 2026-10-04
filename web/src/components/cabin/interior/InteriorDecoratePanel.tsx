import { useMemo, useState } from 'react';
import {
  FURNITURE_CATALOG,
  getFurniture,
  unlockedFurniture,
  type FurnitureDef,
} from './furnitureCatalog';
import { getColorwayCount } from './furnitureArt';
import { MAX_CABIN_LEVEL, deriveCabinLevel, type InteriorItem, type InteriorLayout } from './interiorLayout';

interface InteriorDecoratePanelProps {
  layout: InteriorLayout;
  selectedId: string | null;
  cabinLevel: number;
  /** 是否有未保存的改动（决定是否显示「保存」按钮的高亮）。 */
  dirty: boolean;
  onSelect: (id: string | null) => void;
  onAdd: (furnitureId: string) => void;
  onFlip: (id: string) => void;
  onCycleColorway: (id: string) => void;
  onLayer: (id: string, delta: number) => void;
  onRemove: (id: string) => void;
  onDice: () => void;
  onSave: () => void;
  onReset: () => void;
  onExitEdit: () => void;
  /** 网格吸附开关（Summerhouse：吸附是可选辅助，不是强制）。 */
  snapEnabled: boolean;
  onToggleSnap: (on: boolean) => void;
}

/**
 * 布置模式 UI（编辑态工具面板）。
 *
 * 设计取向（Summerhouse 哲学 + 蔚蓝式即时反馈）：
 *  - 家具栏横滑，点一下就放，**无确认弹窗**；
 *  - 网格吸附是开关而非强制，允许自由叠加；
 *  - 骰子按钮随机投 3 件激发布置灵感；
 *  - 未解锁家具灰显 + 标注解锁条件（不假装可用）。
 */
export function InteriorDecoratePanel(props: InteriorDecoratePanelProps) {
  const {
    layout, selectedId, cabinLevel, dirty, snapEnabled,
    onSelect, onAdd, onFlip, onCycleColorway, onLayer, onRemove,
    onDice, onSave, onReset, onExitEdit, onToggleSnap,
  } = props;

  const [tab, setTab] = useState<'floor' | 'wall' | 'ceiling'>('floor');

  const available = useMemo(
    () => unlockedFurniture({ level: cabinLevel, theme: layout.houseId }),
    [cabinLevel, layout.houseId],
  );
  const availableIds = useMemo(() => new Set(available.map((f) => f.id)), [available]);
  const byMount = useMemo(
    () => FURNITURE_CATALOG.filter((f) => f.mount === tab),
    [tab],
  );
  const selected = selectedId ? layout.items.find((i) => i.id === selectedId) ?? null : null;

  return (
    <section className="cabin-decorate" aria-label="小屋布置模式" data-testid="interior-decorate">
      <header className="cabin-decorate-head">
        <strong>🪑 布置模式</strong>
        <span className="cabin-decorate-level" data-testid="interior-cabin-level">
          小屋 Lv{cabinLevel} / {MAX_CABIN_LEVEL}
        </span>
        <span className="cabin-decorate-count" data-testid="interior-item-count">
          {layout.items.length} 件
        </span>
        <button type="button" className="cabin-btn" onClick={onDice} data-testid="interior-dice">
          🎲 灵感骰子
        </button>
        <label className="cabin-snap-toggle">
          <input
            type="checkbox"
            checked={snapEnabled}
            onChange={(e) => onToggleSnap(e.target.checked)}
            data-testid="interior-snap-toggle"
          />
          网格吸附
        </label>
        <button
          type="button"
          className={dirty ? 'cabin-btn primary' : 'cabin-btn'}
          onClick={onSave}
          disabled={!dirty}
          data-testid="interior-save"
        >
          {dirty ? '保存布置' : '已保存'}
        </button>
        <button type="button" className="cabin-btn" onClick={onReset} data-testid="interior-reset">
          恢复默认
        </button>
        <button type="button" className="cabin-btn ghost" onClick={onExitEdit} data-testid="interior-exit-edit">
          完成
        </button>
      </header>

      <div className="cabin-decorate-tabs" role="tablist" aria-label="家具落位方式">
        {(['floor', 'wall', 'ceiling'] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={tab === m}
            className={tab === m ? 'cabin-chip active' : 'cabin-chip'}
            onClick={() => setTab(m)}
            data-testid={`interior-tab-${m}`}
          >
            {m === 'floor' ? '地板' : m === 'wall' ? '墙面' : '天花板'}
          </button>
        ))}
      </div>

      <div className="cabin-furniture-bar" data-testid="interior-furniture-bar">
        {byMount.map((def) => {
          const unlocked = availableIds.has(def.id);
          const ways = getColorwayCount(def.id);
          return (
            <button
              key={def.id}
              type="button"
              className={unlocked ? 'cabin-furniture-chip' : 'cabin-furniture-chip locked'}
              disabled={!unlocked}
              onClick={() => onAdd(def.id)}
              title={
                unlocked
                  ? `${def.label} · ${def.sizeCells.w}×${def.sizeCells.h} 格 · ${ways} 档配色`
                  : `${def.label} · ${unlockHint(def)}`
              }
              data-testid={`furniture-${def.id}`}
              aria-disabled={!unlocked}
            >
              <span className="cabin-furniture-name">{def.label}</span>
              <span className="cabin-furniture-meta">
                {unlocked ? `${def.sizeCells.w}×${def.sizeCells.h}` : unlockHint(def)}
              </span>
            </button>
          );
        })}
      </div>

      {selected ? (
        <div className="cabin-selection" data-testid="interior-selection">
          <span className="cabin-selection-name">
            {getFurniture(selected.furnitureId)?.label ?? selected.furnitureId}
          </span>
          <button
            type="button"
            className="cabin-btn"
            onClick={() => onFlip(selected.id)}
            data-testid="interior-flip"
          >
            翻转
          </button>
          <button
            type="button"
            className="cabin-btn"
            onClick={() => onCycleColorway(selected.id)}
            data-testid="interior-colorway"
          >
            换配色
          </button>
          <button
            type="button"
            className="cabin-btn"
            onClick={() => onLayer(selected.id, -1)}
            data-testid="interior-layer-down"
          >
            下移一层
          </button>
          <button
            type="button"
            className="cabin-btn"
            onClick={() => onLayer(selected.id, 1)}
            data-testid="interior-layer-up"
          >
            上移一层
          </button>
          <button
            type="button"
            className="cabin-btn danger"
            onClick={() => onRemove(selected.id)}
            data-testid="interior-remove"
          >
            删除
          </button>
        </div>
      ) : (
        <p className="cabin-selection empty" data-testid="interior-selection-empty">
          点击场景里的家具可选中并调整；拖动可移动位置。
          {selectedId !== null && (
            <button
              type="button"
              className="cabin-btn ghost"
              onClick={() => onSelect(null)}
              data-testid="interior-clear-selection"
            >
              取消选中
            </button>
          )}
        </p>
      )}

      <p className="cabin-decorate-note">
        家具可以自由重叠，系统按遮挡关系自动修正前后关系；网格吸附可随时关闭。
        小屋等级由已布置家具数推导（Lv1–Lv{MAX_CABIN_LEVEL}），Lv2 起解锁吊灯与壁架、更高等级解锁大块地毯与炉子。
      </p>
    </section>
  );
}

/** 未解锁原因（诚实标注，不含糊说「即将推出」）。 */
function unlockHint(def: FurnitureDef): string {
  switch (def.unlockedBy) {
    case 'level':
      return `Lv${def.unlockLevel} 解锁`;
    case 'quest':
      return '任务解锁（待接入）';
    case 'craft':
      return '图纸制造（待接入）';
    default:
      return '未解锁';
  }
}

/** 由布局推导等级（页面与本面板共用同一函数，避免两处口径不一致）。 */
export function levelFromLayout(layout: InteriorLayout): number {
  return deriveCabinLevel(layout.items.length);
}

export type { InteriorItem };
