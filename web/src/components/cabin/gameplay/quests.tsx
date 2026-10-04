import type {
  DailyView,
  QuestView,
  TutorialProgress,
  WishView,
} from './balance';
import { currentTutorialStepIndex, mergeDailyQuests } from './balance';

/**
 * W2 · 任务板（新手链 / 每日任务 / 居民愿望 / 制造闭环）
 *
 * 全部进度与可领奖状态以**服务端返回**为准：领奖请求发 `claim` 动作，
 * 由后端判 200 / 409（未达成）/ 422（重复领或步骤不符），前端据此显示真实原因。
 *
 * 诚实原则：
 *   - 进度条用服务端给的 progress，不用前端猜测的增量；
 *   - 未达成不给领奖按钮（disabled + 说明），不做出「点了就能领」的假交互；
 *   - 重复领奖的 409/422 原样透出（见「已领取」文案），不静默变成成功。
 */

export interface QuestBoardProps {
  tutorial: TutorialProgress | null;
  tutorialSteps: readonly QuestView[];
  daily: DailyView | null;
  dailyPool: readonly QuestView[];
  wish: WishView | null;
  busy: boolean;
  onClaim: (kind: 'tutorial' | 'daily' | 'wish', id: string) => void;
}

function ProgressBar({
  value,
  target,
  testId,
}: {
  value: number;
  target: number;
  testId: string;
}) {
  const safeTarget = target > 0 ? target : 1;
  const clamped = Math.max(0, Math.min(value, safeTarget));
  const pct = Math.round((clamped / safeTarget) * 100);
  return (
    <div
      className="w2-bar"
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={safeTarget}
      data-testid={testId}
    >
      <div className="w2-bar-fill" style={{ width: `${pct}%` }} />
      <span className="w2-bar-text">
        {clamped} / {safeTarget}
      </span>
    </div>
  );
}

export function QuestBoard({
  tutorial,
  tutorialSteps,
  daily,
  dailyPool,
  wish,
  busy,
  onClaim,
}: QuestBoardProps) {
  const stepIndex = currentTutorialStepIndex(tutorial);
  const dailies = mergeDailyQuests(daily, dailyPool);

  return (
    <section className="w2-panel" aria-label="任务板" data-testid="w2-quest-board">
      <header className="w2-panel-head">
        <h3 className="w2-panel-title">📋 任务板</h3>
        {tutorial?.completed && (
          <span className="w2-chip ok" data-testid="w2-tutorial-done">
            🌱 新手引导已完成
          </span>
        )}
      </header>

      {/* --- 新手链 --- */}
      <div className="w2-block" data-testid="w2-tutorial-block">
        <h4 className="w2-block-title">新手引导（5 步）</h4>
        {tutorial?.completed ? (
          <ol className="w2-step-list">
            {tutorialSteps.map((s) => (
              <li key={s.id} className="w2-step done" data-testid={`w2-step-${s.id}`}>
                <span className="w2-step-title">{s.title}</span>
                <span className="w2-step-reward">{s.reward_label}</span>
              </li>
            ))}
          </ol>
        ) : stepIndex === null ? (
          <p className="w2-empty">任务数据尚未加载。</p>
        ) : (
          (() => {
            const step = tutorialSteps[stepIndex];
            if (!step) {
              return (
                <p className="w2-empty" data-testid="w2-tutorial-missing">
                  服务端返回的引导步骤序号（{stepIndex}）超出本地步骤表，请刷新页面。
                </p>
              );
            }
            const claimed = (tutorial?.claimed ?? []).includes(step.id);
            // 进度键就是服务端 meta 给的 event 名（explore/feed/decorate/level），
            // 前端不猜、不硬编码「t1 是 explore」这类映射。
            const cur = Number(tutorial?.progress?.[step.event] ?? 0);
            const ready = cur >= step.target;
            return (
              <div className="w2-step current" data-testid={`w2-step-${step.id}`}>
                <div className="w2-step-top">
                  <span className="w2-step-title">
                    第 {stepIndex + 1} 步 · {step.title}
                  </span>
                  <span className="w2-step-reward">{step.reward_label}</span>
                </div>
                {step.desc && <p className="w2-step-desc">{step.desc}</p>}
                <ProgressBar
                  value={cur}
                  target={step.target}
                  testId={`w2-step-${step.id}-bar`}
                />
                <button
                  type="button"
                  className="w2-btn primary"
                  data-testid={`w2-claim-${step.id}`}
                  disabled={busy || !ready || claimed}
                  aria-disabled={!ready || claimed}
                  onClick={() => onClaim('tutorial', step.id)}
                >
                  {claimed ? '已领取' : ready ? '领取奖励' : `还差 ${step.target - cur} 次`}
                </button>
              </div>
            );
          })()
        )}
      </div>

      {/* --- 每日任务 --- */}
      <div className="w2-block" data-testid="w2-daily-block">
        <h4 className="w2-block-title">
          今日任务（{daily?.date ?? '—'} · 每日 0 点按 UTC+8 重置）
        </h4>
        {dailies.length === 0 ? (
          <p className="w2-empty" data-testid="w2-daily-empty">
            今日任务尚未生成（服务端未返回）。
          </p>
        ) : (
          <ul className="w2-quest-list">
            {dailies.map((q) => (
              <li key={q.id} className="w2-quest" data-testid={`w2-daily-${q.id}`}>
                <div className="w2-quest-top">
                  <span className="w2-quest-title">{q.title}</span>
                  <span className="w2-quest-reward">{q.reward_label}</span>
                </div>
                <ProgressBar value={q.progress} target={q.target} testId={`w2-daily-${q.id}-bar`} />
                <button
                  type="button"
                  className="w2-btn primary"
                  data-testid={`w2-claim-${q.id}`}
                  disabled={busy || !q.ready}
                  aria-disabled={!q.ready}
                  onClick={() => onClaim('daily', q.id)}
                >
                  {q.claimed ? '已领取' : q.ready ? '领取奖励' : `还差 ${q.target - q.progress}`}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* --- 居民愿望 --- */}
      <div className="w2-block" data-testid="w2-wish-block">
        <h4 className="w2-block-title">居民愿望</h4>
        {!wish ? (
          <p className="w2-empty">今天还没有人许愿。</p>
        ) : (
          <div className="w2-quest wish" data-testid={`w2-wish-${wish.id}`}>
            <p className="w2-wish-text">「{wish.text}」</p>
            <div className="w2-quest-top">
              <span className="w2-quest-reward">{wish.reward_label}</span>
              <span className="w2-chip">
                {wish.kind === 'material' ? '材料心愿' : '家具心愿'}
              </span>
            </div>
            <ProgressBar
              value={Number(wish.progress ?? 0)}
              target={Number(wish.need ?? 1)}
              testId={`w2-wish-${wish.id}-bar`}
            />
            <button
              type="button"
              className="w2-btn primary"
              data-testid={`w2-claim-wish-${wish.id}`}
              disabled={busy || !wish.done || wish.claimed}
              aria-disabled={!wish.done || wish.claimed}
              onClick={() => onClaim('wish', wish.id)}
            >
              {wish.claimed ? '已领取' : wish.done ? '满足愿望' : '尚未满足'}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * 制造闭环面板（DNA-7：材料 → 图纸 → 家具解锁）。
 *
 * 打造请求发 `craft` 动作；是否够料、成功解锁了什么**全由后端判定**。
 * 前端只用 `blueprints` 展示所需材料并标出还差什么。
 */
export interface CraftPanelProps {
  blueprints: Record<string, { label: string; cost: Record<string, number>; unlock_level: number }>;
  materials: Record<string, number>;
  materialLabels: Record<string, string>;
  houseLevel: number;
  unlockedFurniture: readonly string[];
  busy: boolean;
  onCraft: (furnitureId: string) => void;
}

export function CraftPanel({
  blueprints,
  materials,
  materialLabels,
  houseLevel,
  unlockedFurniture,
  busy,
  onCraft,
}: CraftPanelProps) {
  const entries = Object.entries(blueprints ?? {});
  if (entries.length === 0) {
    return (
      <section className="w2-panel" aria-label="制造" data-testid="w2-craft-panel">
        <p className="w2-empty">图纸数据尚未加载。</p>
      </section>
    );
  }

  return (
    <section className="w2-panel" aria-label="制造" data-testid="w2-craft-panel">
      <header className="w2-panel-head">
        <h3 className="w2-panel-title">🔨 制造 · 材料换家具</h3>
      </header>
      <ul className="w2-craft-list">
        {entries.map(([fid, bp]) => {
          const alreadyUnlocked = (unlockedFurniture ?? []).includes(fid);
          const lacks = Object.entries(bp.cost ?? {}).filter(
            ([mid, need]) => (materials?.[mid] ?? 0) < need,
          );
          const levelOk = houseLevel >= (bp.unlock_level ?? 1);
          const canCraft = lacks.length === 0 && levelOk && !alreadyUnlocked;
          return (
            <li key={fid} className="w2-craft" data-testid={`w2-craft-${fid}`}>
              <div className="w2-craft-top">
                <span className="w2-craft-name">{bp.label}</span>
                {alreadyUnlocked && (
                  <span className="w2-chip ok" data-testid={`w2-craft-${fid}-owned`}>
                    已解锁
                  </span>
                )}
              </div>
              <ul className="w2-cost-list">
                {Object.entries(bp.cost ?? {}).map(([mid, need]) => {
                  const have = materials?.[mid] ?? 0;
                  const ok = have >= need;
                  return (
                    <li
                      key={mid}
                      className={ok ? 'w2-cost ok' : 'w2-cost lack'}
                      data-testid={`w2-cost-${fid}-${mid}`}
                    >
                      {materialLabels[mid] ?? mid} {have}/{need}
                    </li>
                  );
                })}
              </ul>
              {!levelOk && (
                <p className="w2-craft-lock" data-testid={`w2-craft-${fid}-level`}>
                  需要小屋 Lv{bp.unlock_level}（当前 Lv{houseLevel}）
                </p>
              )}
              <button
                type="button"
                className="w2-btn primary"
                data-testid={`w2-craft-btn-${fid}`}
                disabled={busy || !canCraft}
                aria-disabled={!canCraft}
                onClick={() => onCraft(fid)}
              >
                {alreadyUnlocked
                  ? '已解锁'
                  : !levelOk
                    ? `需 Lv{bp.unlock_level}`
                    : lacks.length > 0
                      ? `还差 ${lacks.length} 种材料`
                      : '打造'}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
