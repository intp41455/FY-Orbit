import type { CabinSaveView } from './balance';
import {
  MAX_MATERIAL_QTY,
  formatNumber,
  groupMaterialsByTheme,
  nextLevelProgress,
  visibleMaterials,
} from './balance';

/**
 * W2 · 背包与小屋状态面板
 *
 * 纯展示：所有数量来自服务端 `materials` / `coins` / `intimacy` / `house_level`，
 * 前端不做任何增量计算（那会变成第二套结算，是作弊与不一致的源头）。
 *
 * 诚实原则：
 *   - 背包为空时明说「还没有材料」并提示去探险，不用假数据填满格子；
 *   - 背包满（99）如实标注「已满」，不隐藏溢出；
 *   - 亲密度条只画服务端给的数值，不显示「+5」之类前端自造的变化。
 */

export interface BackpackPanelProps {
  save: CabinSaveView;
  /** 当前小屋家具件数（由 W1 的 layout.items.length 派生，与后端 house_level 对齐）。 */
  furnitureCount: number;
}

/** 顶部数值条：金币 / 亲密度 / 小屋等级 / 连续登录 / 钥匙。 */
export function StatusBar({ save, furnitureCount }: BackpackPanelProps) {
  const lv = nextLevelProgress(furnitureCount, save.house_level);
  return (
    <section className="w2-status" aria-label="小屋状态" data-testid="w2-status-bar">
      <div className="w2-stat" data-testid="w2-stat-coins">
        <span className="w2-stat-label">金币</span>
        <span className="w2-stat-value">{formatNumber(save.coins)}</span>
      </div>
      <div className="w2-stat" data-testid="w2-stat-intimacy">
        <span className="w2-stat-label">亲密度</span>
        <span className="w2-stat-value">{formatNumber(save.intimacy)} / 100</span>
      </div>
      <div className="w2-stat" data-testid="w2-stat-level">
        <span className="w2-stat-label">小屋</span>
        <span className="w2-stat-value">
          Lv{save.house_level}
          {lv.nextLevel ? ` · 下一级需 ${lv.need} 件（现 ${furnitureCount}）` : ' · 已满级'}
        </span>
      </div>
      <div className="w2-stat" data-testid="w2-stat-streak">
        <span className="w2-stat-label">连续登录</span>
        <span className="w2-stat-value">{formatNumber(save.login_streak)} 天</span>
      </div>
      <div className="w2-stat" data-testid="w2-stat-keys">
        <span className="w2-stat-label">宝箱钥匙</span>
        <span className="w2-stat-value">{formatNumber(save.chest_keys)}</span>
      </div>
      {save.dust.length > 0 && (
        <div className="w2-stat" data-testid="w2-stat-dust">
          <span className="w2-stat-label">落灰探险点</span>
          <span className="w2-stat-value">{save.dust.length} 处待打扫</span>
        </div>
      )}
    </section>
  );
}

export function BackpackPanel({ save, furnitureCount }: BackpackPanelProps) {
  const items = visibleMaterials(save);
  const groups = groupMaterialsByTheme(items);
  void furnitureCount;

  return (
    <section className="w2-panel" aria-label="背包" data-testid="w2-backpack">
      <header className="w2-panel-head">
        <h3 className="w2-panel-title">🎒 背包</h3>
        <span className="w2-panel-meta" data-testid="w2-backpack-count">
          {items.length} 种材料
        </span>
      </header>

      {items.length === 0 ? (
        <p className="w2-empty" data-testid="w2-backpack-empty">
          背包还是空的——去探险点采集材料吧。
        </p>
      ) : (
        groups.map((g) => (
          <div key={g.theme} className="w2-mat-group" data-testid={`w2-mat-group-${g.theme}`}>
            <h4 className="w2-mat-group-title">{g.label}</h4>
            <ul className="w2-mat-grid">
              {g.items.map((m) => (
                <li
                  key={m.id}
                  className={m.capped ? 'w2-mat capped' : 'w2-mat'}
                  data-testid={`w2-mat-${m.id}`}
                  title={m.capped ? `已达上限 ${MAX_MATERIAL_QTY}` : undefined}
                >
                  <span className="w2-mat-name">{m.label}</span>
                  <span className="w2-mat-qty">
                    {m.capped ? `${m.qty} · 满` : `×${m.qty}`}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}

/**
 * 照料动作条（喂食 / 浇水 / 打扫）。
 *
 * 「命中喜好加成翻倍」由服务端判定——这里只把后端返回的
 * `preferences` 展示出来，且 `recognized=false` 时**不说**「命中喜好」。
 */
export interface CareBarProps {
  preferences: CabinSaveView['preferences'];
  busy: boolean;
  onFeed: () => void;
  onWater: () => void;
  onClean: () => void;
}

export function CareBar({ preferences, busy, onFeed, onWater, onClean }: CareBarProps) {
  const hintPrefix = preferences?.recognized ? '他喜欢' : '默认喜好（性格未识别）';
  return (
    <section className="w2-panel" aria-label="日常照料" data-testid="w2-care-bar">
      <header className="w2-panel-head">
        <h3 className="w2-panel-title">🫧 日常照料</h3>
        {preferences && (
          <span
            className="w2-panel-meta"
            data-testid="w2-pref-recognized"
            data-recognized={preferences.recognized ? 'true' : 'false'}
          >
            {preferences.recognized ? '性格已识别' : '性格未识别'}
          </span>
        )}
      </header>
      <div className="w2-care-actions">
        <button
          type="button"
          className="w2-btn primary"
          data-testid="w2-feed"
          disabled={busy}
          onClick={onFeed}
        >
          🍚 喂食
        </button>
        <button
          type="button"
          className="w2-btn"
          data-testid="w2-water"
          disabled={busy}
          onClick={onWater}
        >
          💧 浇水
        </button>
        <button
          type="button"
          className="w2-btn"
          data-testid="w2-clean"
          disabled={busy}
          onClick={onClean}
        >
          🧹 打扫
        </button>
      </div>
      {preferences && (
        <ul className="w2-pref-list">
          <li data-testid="w2-pref-person">
            小人：{hintPrefix}
            {preferences.person.food_label}——{preferences.person.note}
          </li>
          <li data-testid="w2-pref-pet">
            宠物：{hintPrefix}
            {preferences.pet.food_label}——{preferences.pet.note}
          </li>
        </ul>
      )}
    </section>
  );
}
