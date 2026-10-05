import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, NetworkError } from '../../../api/client';
import { FURNITURE_CATALOG } from '../interior/furnitureCatalog';
import type { GameplayMeta } from './cabinGameplayApi';
import { fetchGameplayMeta, fetchSave, postAction } from './cabinGameplayApi';
import { BackpackPanel, CareBar, StatusBar } from './backpack';
import type { CabinSaveView, EventView, QuestView, ThemeId } from './balance';
import { THEME_LABELS, THEME_ORDER, resolveUnlock } from './balance';
import { CompanionBar, EventCard, OfflineBar } from './events';
import { CraftPanel, QuestBoard } from './quests';
import { SpotsPanel } from './spots';

/**
 * W2 · 玩法主容器（数码小屋玩法循环 / 背景探险 / 日常系统）
 *
 * 职责边界：
 *   - 本组件**不做任何结算**。每次交互只发一个 `/action` 请求，
 *     然后用服务端返回的整份存档覆盖本地 state（服务端权威）。
 *   - 前端唯一的「本地计算」是展示派生：家具解锁态（复用 W1 furnitureCatalog
 *     的 unlockedBy 契约 + 服务端下发的 unlocked_furniture 白名单）。
 *
 * 诚实原则：
 *   - 每个动作失败都显示后端原文（409 冷却 / 422 未达成 / 网络失败），
 *     并明确「本次操作未生效」；
 *   - meta 或 save 任一加载失败 → 对应面板显示真实错误，不渲染假数据；
 *   - busy 期间禁用按钮，避免重复提交造成双花（服务端有 version 乐观锁兜底）。
 */

export interface GameplayPanelProps {
  personality: string;
  personName: string;
  /** W1 布局里的家具件数，用于小屋等级展示（与后端 house_level 同源阈值）。 */
  furnitureCount: number;
  initialTheme?: ThemeId;
  showCoins?: boolean;
}

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'error'; text: string };

function errText(err: unknown): string {
  if (err instanceof ApiError) {
    const code = err.body?.code ? `（${err.body.code}）` : '';
    return `${err.message}${code}`;
  }
  if (err instanceof NetworkError) {
    return `网络不可用（${err.kind}）：${err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

export function GameplayPanel({
  personality,
  personName,
  furnitureCount,
  initialTheme = 'forest',
  showCoins = true,
}: GameplayPanelProps) {
  const [theme, setTheme] = useState<ThemeId>(initialTheme);
  const [save, setSave] = useState<CabinSaveView | null>(null);
  const [meta, setMeta] = useState<GameplayMeta | null>(null);
  const [saveState, setSaveState] = useState<LoadState>({ kind: 'loading' });
  const [metaState, setMetaState] = useState<LoadState>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [event, setEvent] = useState<EventView | null>(null);
  const [eventFeedback, setEventFeedback] = useState<string | null>(null);
  const [showOffline, setShowOffline] = useState(false);
  const [unlockNote, setUnlockNote] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /* --- 加载 meta（数值表单一真源；失败不影响 save 面板） --- */
  const loadMeta = useCallback(() => {
    setMetaState({ kind: 'loading' });
    void fetchGameplayMeta()
      .then((m) => {
        if (!mounted.current) return;
        setMeta(m);
        setMetaState({ kind: 'ready' });
      })
      .catch((err: unknown) => {
        if (!mounted.current) return;
        setMetaState({ kind: 'error', text: `玩法元数据加载失败：${errText(err)}` });
      });
  }, []);

  /* --- 加载存档（这一步会触发每日重置 / 离线结算 / 自主行为 roll） --- */
  const loadSave = useCallback(
    (t: ThemeId = theme) => {
      setSaveState({ kind: 'loading' });
      void fetchSave({ theme: t, personality, person_name: personName })
        .then((s) => {
          if (!mounted.current) return;
          setSave(s);
          setSaveState({ kind: 'ready' });
          setShowOffline(Boolean(s.offline));
          if (s.level_up) {
            setUnlockNote(`小屋升到了 Lv${s.level_up.to}（由服务端结算）`);
          }
        })
        .catch((err: unknown) => {
          if (!mounted.current) return;
          setSaveState({ kind: 'error', text: `存档读取失败：${errText(err)}` });
        });
    },
    [theme, personality, personName],
  );

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    loadSave(theme);
    // 仅在身份变化时重载；theme 切换由下方 handler 触发，避免重复请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [personality, personName]);

  /* --- 执行动作：唯一的状态变更入口 --- */
  const act = useCallback(
    (body: Parameters<typeof postAction>[0], opts?: { showEvent?: boolean }) => {
      setBusy(true);
      setActionError(null);
      void postAction({ ...body, personality, person_name: personName })
        .then((res) => {
          if (!mounted.current) return;
          setBusy(false);
          // 服务端返回整份存档 → 直接覆盖（服务端权威）。
          if (res.version !== undefined && res.materials !== undefined) {
            setSave((prev) => ({ ...(prev as CabinSaveView), ...(res as CabinSaveView) }));
            setSaveState({ kind: 'ready' });
          } else {
            // 兜底：动作回执不完整时重新拉一次存档，保证不展示过期数值。
            loadSave(theme);
          }
          if (opts?.showEvent) {
            const ev = (res.event ?? null) as EventView | null;
            setEvent(ev);
            setEventFeedback((res.feedback as string | undefined) ?? null);
          }
        })
        .catch((err: unknown) => {
          if (!mounted.current) return;
          setBusy(false);
          // 诚实：明确告知本次操作未生效，并展示后端原文。
          setActionError(`本次操作未生效：${errText(err)}`);
        });
    },
    [personality, personName, loadSave, theme],
  );

  const handleTheme = useCallback(
    (t: ThemeId) => {
      setTheme(t);
      setEvent(null);
      setActionError(null);
      loadSave(t);
    },
    [loadSave],
  );

  const onExplore = useCallback((spotId: string) => act({ action: 'explore', spot_id: spotId }, { showEvent: true }), [act]);
  const onWaterSpot = useCallback((spotId: string) => act({ action: 'water', spot_id: spotId }, { showEvent: true }), [act]);
  const onCleanSpot = useCallback((spotId: string) => act({ action: 'clean', spot_id: spotId }, { showEvent: true }), [act]);
  const onFeed = useCallback(() => act({ action: 'feed' }, { showEvent: true }), [act]);
  const onWater = useCallback(() => act({ action: 'water' }, { showEvent: true }), [act]);
  const onClean = useCallback(() => act({ action: 'clean' }, { showEvent: true }), [act]);
  const onCraft = useCallback((target: string) => act({ action: 'craft', target }), [act]);
  const onClaim = useCallback(
    (quest_kind: 'tutorial' | 'daily' | 'wish', quest_id: string) =>
      act({ action: 'claim', quest_kind, quest_id }, { showEvent: true }),
    [act],
  );

  /* --- 家具解锁态（复用 W1 契约 + 服务端白名单） --- */
  const unlockStates = useMemo(() => {
    const has = (id: string) => save?.materials?.[id] ?? 0;
    return FURNITURE_CATALOG.map((f) => ({
      def: f,
      state: resolveUnlock(
        {
          id: f.id,
          label: f.label,
          unlockedBy: f.unlockedBy,
          unlockLevel: f.unlockLevel,
          themes: f.themes,
        },
        {
          houseLevel: save?.house_level ?? 1,
          unlockedFurniture: save?.unlocked_furniture ?? [],
          hasMaterials: has,
        },
      ),
    }));
  }, [save?.house_level, save?.materials, save?.unlocked_furniture]);

  const questOrCraftLocked = unlockStates.filter(
    (u) => (u.def.unlockedBy === 'quest' || u.def.unlockedBy === 'craft') && !u.state.unlocked,
  );

  /* --- meta → QuestView 适配 --- */
  const tutorialSteps: QuestView[] = useMemo(
    () =>
      (meta?.tutorial_steps ?? []).map((s) => ({
        id: s.id,
        title: s.title,
        event: s.event,
        target: s.target,
        reward_label: s.reward_label,
        desc: s.desc,
      })),
    [meta?.tutorial_steps],
  );
  const dailyPool: QuestView[] = useMemo(
    () =>
      (meta?.daily_pool ?? []).map((d) => ({
        id: d.id,
        title: d.title,
        event: d.event,
        target: d.target,
        reward_label: d.reward_label,
        desc: d.desc,
      })),
    [meta?.daily_pool],
  );
  const materialLabels = useMemo(() => {
    const m: Record<string, string> = {};
    for (const item of meta?.materials ?? []) m[item.id] = item.label;
    return m;
  }, [meta?.materials]);

  return (
    <div className="w2-root" data-testid="w2-gameplay">
      {/* 主题页签 */}
      <nav className="w2-tabs" aria-label="探险主题" data-testid="w2-theme-tabs">
        {THEME_ORDER.map((t) => (
          <button
            key={t}
            type="button"
            className={theme === t ? 'w2-tab active' : 'w2-tab'}
            aria-pressed={theme === t}
            data-testid={`w2-theme-${t}`}
            onClick={() => handleTheme(t)}
          >
            {THEME_LABELS[t]}
          </button>
        ))}
      </nav>

      {/* 全局错误 / 提示 */}
      {actionError && (
        <p className="w2-error" role="alert" data-testid="w2-action-error">
          {actionError}
        </p>
      )}
      {unlockNote && (
        <p className="w2-note" role="status" data-testid="w2-unlock-note">
          {unlockNote}
        </p>
      )}

      {/* 离线收益 */}
      {showOffline && save?.offline && (
        <OfflineBar
          awayHours={save.offline.away_hours}
          realAwayHours={save.offline.real_away_hours}
          capped={save.offline.capped}
          refreshedSpots={save.offline.refreshed_spots}
          text={save.offline.text}
          onDismiss={() => setShowOffline(false)}
        />
      )}

      {/* 宠物自主行为 */}
      {save?.companion && (
        <CompanionBar
          behavior={save.companion.behavior}
          label={save.companion.label}
          awayHours={save.companion.away_hours}
          trinkets={save.companion.trinkets}
          unlockedCount={save.companion.unlocked_count}
        />
      )}

      {/* 随机事件弹层 */}
      <EventCard event={event} feedback={eventFeedback} onDismiss={() => setEvent(null)} />

      {/* save 加载失败 → 明确报错，不渲染任何假面板 */}
      {saveState.kind === 'error' && (
        <p className="w2-error" role="alert" data-testid="w2-save-error">
          {saveState.text}
        </p>
      )}
      {saveState.kind === 'loading' && (
        <p className="w2-loading" role="status" data-testid="w2-save-loading">
          正在读取存档并结算离线收益…
        </p>
      )}

      {save && saveState.kind === 'ready' && (
        <>
          <StatusBar save={save} furnitureCount={furnitureCount} showCoins={showCoins} />

          <div className="w2-grid">
            <SpotsPanel
              spots={save.spots}
              theme={theme}
              busy={busy}
              onExplore={onExplore}
              onWater={onWaterSpot}
              onClean={onCleanSpot}
            />
            <div className="w2-col">
              <CareBar
                preferences={save.preferences}
                busy={busy}
                onFeed={onFeed}
                onWater={onWater}
                onClean={onClean}
              />
              <BackpackPanel save={save} furnitureCount={furnitureCount} />
            </div>
          </div>

          {metaState.kind === 'error' && (
            <p className="w2-error" role="alert" data-testid="w2-meta-error">
              {metaState.text}（任务与制造面板暂不可用）
            </p>
          )}
          {meta && metaState.kind === 'ready' && (
            <>
              <QuestBoard
                tutorial={save.quests.tutorial}
                tutorialSteps={tutorialSteps}
                daily={save.quests.daily}
                dailyPool={dailyPool}
                wish={save.quests.wish}
                busy={busy}
                onClaim={onClaim}
              />
              <CraftPanel
                blueprints={meta.blueprints}
                materials={save.materials}
                materialLabels={materialLabels}
                houseLevel={save.house_level}
                unlockedFurniture={save.unlocked_furniture}
                busy={busy}
                onCraft={onCraft}
              />
            </>
          )}

          {/* quest/craft 类家具解锁提示（诚实：未解锁就说清原因） */}
          <section className="w2-panel" aria-label="解锁家具" data-testid="w2-unlock-panel">
            <header className="w2-panel-head">
              <h3 className="w2-panel-title">🔓 任务 / 图纸解锁家具</h3>
              <span className="w2-panel-meta" data-testid="w2-unlock-progress">
                {FURNITURE_CATALOG.length - questOrCraftLocked.length} / {FURNITURE_CATALOG.length} 已解锁
              </span>
            </header>
            <ul className="w2-unlock-list">
              {unlockStates
                .filter((u) => u.def.unlockedBy === 'quest' || u.def.unlockedBy === 'craft')
                .map((u) => (
                  <li
                    key={u.def.id}
                    className={u.state.unlocked ? 'w2-unlock ok' : 'w2-unlock locked'}
                    data-testid={`w2-unlock-${u.def.id}`}
                    data-unlocked={u.state.unlocked ? 'true' : 'false'}
                  >
                    <span className="w2-unlock-name">{u.def.label}</span>
                    {u.state.unlocked ? (
                      <span className="w2-unlock-state ok">已解锁，可在布置模式摆放</span>
                    ) : (
                      <span className="w2-unlock-state">
                        {u.state.reason} · {u.state.hint}
                      </span>
                    )}
                  </li>
                ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
