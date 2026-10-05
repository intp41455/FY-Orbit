import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CabinStage, type CabinSpeechRequest } from '../components/cabin/CabinStage';
import {
  CABIN_BACKGROUNDS,
  CABIN_HOUSES,
  PERSONALITIES,
  PET_COLORS,
  resolveDialogue,
  useCabinConfig,
  type DialogueSpeaker,
  type PersonalityId,
} from '../components/cabin/cabinConfig';
import { cabinDialogue, type DialogueSource } from '../components/cabin/cabinDialogueProvider';
import { InteriorStage } from '../components/cabin/interior/InteriorStage';
import { InteriorDecoratePanel } from '../components/cabin/interior/InteriorDecoratePanel';
import {
  addItem,
  cycleColorway,
  defaultLayout,
  deriveCabinLevel,
  flipItem,
  isLayoutTooLarge,
  loadLocalLayout,
  MAX_ITEMS,
  moveItem,
  removeItem,
  rollDiceFurniture,
  saveLocalLayout,
  shiftLayer,
  type InteriorItem,
  type InteriorLayout,
} from '../components/cabin/interior/interiorLayout';
import { loadInteriorWithCache, saveInterior } from '../components/cabin/interior/cabinInteriorApi';
import {
  PLACE_FEEDBACK_LINES,
  resolveFurnitureInteraction,
} from '../components/cabin/interior/furnitureInteraction';
import { getFurniture } from '../components/cabin/interior/furnitureCatalog';
import { GameplayPanel } from '../components/cabin/gameplay/GameplayPanel';
import {
  avatarErrorCode,
  getHouseAvatar,
  isAvatarNotFound,
  type HouseAvatar,
} from '../api/avatar';
import { toPixelPalette, walkFrames } from '../components/avatar/avatarPixels';
// W9 个人资产库联动（墙面挂画 + BGM）。只读 /api/assets，不改 cabinScene 内部。
import {
  CabinBgmCard,
  CabinWallArt,
  CabinWallArtCard,
  useCabinMedia,
} from '../components/assets/CabinMedia';
import { assetsApi, type AssetRecord } from '../api/assets';
import { ApiError } from '../api/client';
import { CabinHud } from '../components/cabin/hud/CabinHud';
import { lifeApi, type LifeSnapshot, type GatherRow } from '../components/cabin/gameplay/lifeApi';
import {
  GatherAnimationPlayer,
  buildThreeSecondPrompt,
} from '../components/cabin/gameplay/cabinGatherInteraction';
import {
  getHouseRooms,
  unlockRoom,
  createRoomLayout,
  globalTransitionManager,
  executeSleep,
} from '../components/cabin/interior/cabinHouseSystem';
// 包 D 视觉层：玻璃 HUD 覆盖层 + 像素/玻璃边界样式。
import '../styles/pages/cabin.css';
import {
  CabinHudOverlay,
  npcRowsFromSnapshot,
  type CabinNpcRow,
} from '../components/cabinni/CabinHudOverlay';

/** 台词来源诚实标注：模型生成 / 预生成台词池（未探测时默认 provider 本就是池）。 */
const DIALOGUE_SOURCE_LABEL: Record<DialogueSource, string> = {
  model: '模型生成',
  pool: '预生成台词池',
};

/** 布置保存状态（诚实区分：云端已存 / 仅本地 / 保存失败）。 */
type SaveState =
  | { kind: 'idle'; text: string }
  | { kind: 'local'; text: string }
  | { kind: 'saved'; text: string }
  | { kind: 'error'; text: string };

/** 落位时随机一句即时反馈（≤100ms 手感；预生成池，非模型生成）。 */
function placeFeedback(rng: () => number): string {
  const i = Math.min(PLACE_FEEDBACK_LINES.length - 1, Math.max(0, Math.floor(rng() * PLACE_FEEDBACK_LINES.length)));
  return PLACE_FEEDBACK_LINES[i] ?? '';
}

/**
 * 为新家具找一个「不与现有同 mount 家具同格」的落点。
 * 从中部往下找，找不到返回 null（由调用方决定兜底位置）。
 */
function findFreeSpot(
  layout: InteriorLayout,
  furnitureId: string,
): { x: number; y: number } | null {
  const def = getFurniture(furnitureId);
  if (!def) return null;
  const taken = layout.items.filter((it) => getFurniture(it.furnitureId)?.mount === def.mount);
  for (let y = 1; y <= 6; y += 1) {
    for (let x = 1; x <= 24; x += 1) {
      if (!taken.some((it) => it.x === x && it.y === y)) return { x, y };
    }
  }
  return null;
}

/**
 * 我的小屋（P2）：全屏 PixiJS 经营模拟生活小游戏脚手架。
 * 数码小人 + 小屋 + 宠物；房屋/背景/宠物颜色/性格/名字全部实时生效并持久化。
 * 台词通道：挂载时探测 /api/butler/status，已配置模型时走管家 Agent（标注「模型生成」），
 * 未配置/失败时诚实回退本地台词池（标注「预生成台词池」），见 cabinDialogueProvider.ts。
 */
export function CabinPage() {
  const navigate = useNavigate();
  const [config, update] = useCabinConfig();
  const [speech, setSpeech] = useState<CabinSpeechRequest | null>(null);
  const speechSeq = useRef(0);
  const [dialogueSource, setDialogueSource] = useState<DialogueSource | null>(
    cabinDialogue.getSource(),
  );
  const configRef = useRef(config);
  configRef.current = config;

  useEffect(() => {
    const unsubscribe = cabinDialogue.subscribe(setDialogueSource);
    void cabinDialogue.install({
      context: () => ({ user_name: configRef.current.personName }),
    });
    return unsubscribe;
  }, []);

  const handleSpeak = (speaker: DialogueSpeaker) => {
    const personality = speaker === 'person' ? config.personPersonality : config.petPersonality;
    speechSeq.current += 1;
    const seq = speechSeq.current;
    void Promise.resolve(resolveDialogue(speaker, personality))
      .then((text) => setSpeech({ speaker, text, seq }))
      .catch(() => {
        // 接线 provider 内部已做池回退，这里只是最后防线：异常时跳过本次台词，不打断游戏。
      });
  };

  /* ------------------------------------------------------------------ */
  /* B 包 · 生活模拟与 HUD 真实快照数据                                  */
  /* ------------------------------------------------------------------ */
  const [lifeSnapshot, setLifeSnapshot] = useState<LifeSnapshot | null>(null);
  const [lifeLoading, setLifeLoading] = useState(false);
  const [lifeError, setLifeError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLifeLoading(true);

    const loadLife = async () => {
      try {
        const snap = await lifeApi.fetchSnapshot();
        if (!cancelled) {
          setLifeSnapshot(snap);
          setLifeError(null);
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          try {
            await lifeApi.createSave(config.background, 1);
            const snap = await lifeApi.fetchSnapshot();
            if (!cancelled) {
              setLifeSnapshot(snap);
              setLifeError(null);
            }
            return;
          } catch {
            // 建档异常由外部统一兜底展示
          }
        }
        if (!cancelled) {
          setLifeError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) {
          setLifeLoading(false);
        }
      }
    };

    void loadLife();
    return () => {
      cancelled = true;
    };
  }, [config.background]);

  const handleLifeAction = async (action: string, args?: Record<string, unknown>) => {
    try {
      const res = await lifeApi.act(action, args);
      const snap = await lifeApi.fetchSnapshot();
      setLifeSnapshot(snap);
      setLifeError(null);
      return res;
    } catch (err) {
      setLifeError(err instanceof Error ? err.message : String(err));
      throw err;
    }
  };

  const animPlayer = useMemo(() => new GatherAnimationPlayer(), []);
  const activeGatherNode = useMemo(
    () => lifeSnapshot?.gather?.find((r) => r.in_range) ?? null,
    [lifeSnapshot?.gather],
  );
  const threeSecondCard = useMemo(
    () => (activeGatherNode ? buildThreeSecondPrompt(activeGatherNode) : null),
    [activeGatherNode],
  );

  const handleGather = useCallback(
    async (node: GatherRow) => {
      animPlayer.play(node.action);
      await handleLifeAction('gather', { node_id: node.id });
    },
    [animPlayer],
  );

  /* ------------------------------------------------------------------ */
  /* W1 · 室内场景与布置                                                 */
  /* ------------------------------------------------------------------ */

  const [view, setView] = useState<'outdoor' | 'indoor'>('outdoor');
  const [layout, setLayout] = useState<InteriorLayout>(() => defaultLayout(config.house));
  /** 后端权威版本（乐观锁）。与本地草稿的 layout.version 分开维护。 */
  const [serverVersion, setServerVersion] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle', text: '' });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [glowToken, setGlowToken] = useState<{ itemId: string; token: number } | null>(null);
  const [sleepToken, setSleepToken] = useState(0);
  const [sayToken, setSayToken] = useState<{ text: string; token: number } | null>(null);
  const [personToken, setPersonToken] = useState<{ gx: number; gy: number; token: number } | null>(null);
  const glowSeq = useRef(0);
  const saySeq = useRef(0);
  const personSeq = useRef(0);
  /** 已自动回写过后端默认布置的房间，避免每次进屋重复 PUT。 */
  const seededRef = useRef(new Set<string>());

  const cabinLevel = deriveCabinLevel(layout.items.length);
  const selected = selectedId ? layout.items.find((i) => i.id === selectedId) ?? null : null;
  const selectedLabel = selected ? (getFurniture(selected.furnitureId)?.label ?? selected.furnitureId) : null;

  const [currentRoomId, setCurrentRoomId] = useState<string>('living');
  const [unlockedRoomIds, setUnlockedRoomIds] = useState<string[]>(['living']);

  const handleSwitchRoom = useCallback((roomId: string) => {
    setCurrentRoomId(roomId);
    if (roomId === 'living') {
      const local = loadLocalLayout(config.house);
      setLayout(local ?? defaultLayout(config.house));
    } else {
      const local = loadLocalLayout(`${config.house}:${roomId}`);
      setLayout(local ?? createRoomLayout(config.house, roomId));
    }
    setSelectedId(null);
    setDirty(false);
  }, [config.house]);

  const handleExpandRoom = useCallback((roomId: string) => {
    const currentCoins = lifeSnapshot?.save?.coins ?? 0;
    const res = unlockRoom(roomId, cabinLevel, currentCoins, unlockedRoomIds);
    if (!res.success) {
      setSaveState({ kind: 'error', text: res.reason ?? '扩建失败' });
      return;
    }
    setUnlockedRoomIds(res.newUnlocked);
    handleSwitchRoom(roomId);
    setSaveState({ kind: 'saved', text: `成功扩建房间，消耗金币已结算` });
  }, [cabinLevel, lifeSnapshot, unlockedRoomIds, handleSwitchRoom]);

  /** 切换房屋模板 → 切到该模板对应的布置（任务书第 4 条）。 */
  useEffect(() => {
    if (view !== 'indoor') return;
    let cancelled = false;
    const houseId = config.house;
    // 先给本地缓存/默认值，保证画面立刻可用（后端为准，稍后覆盖）。
    const local = loadLocalLayout(houseId);
    setLayout(local ?? defaultLayout(houseId));
    setSelectedId(null);
    setDirty(false);
    setLoadError(null);
    setSaveState({ kind: 'idle', text: '' });

    void loadInteriorWithCache(houseId)
      .then(({ layout: fromServer, fromBackend }) => {
        if (cancelled) return;
        if (fromBackend && fromServer) {
          setLayout(fromServer);
          setServerVersion(fromServer.version);
          saveLocalLayout(fromServer);
          return;
        }
        // 后端还没有存档：用本地默认布局并回写一次，让后端成为真源。
        const fresh = local ?? defaultLayout(houseId);
        setLayout(fresh);
        setServerVersion(0);
        if (seededRef.current.has(houseId)) return;
        seededRef.current.add(houseId);
        void saveInterior(houseId, fresh, 0)
          .then((res) => {
            if (cancelled) return;
            setServerVersion(res.version);
            setSaveState({ kind: 'saved', text: '默认布置已保存到云端' });
          })
          .catch(() => {
            if (cancelled) return;
            // 默认布局回写失败不影响本次游玩，如实说明状态。
            setSaveState({ kind: 'error', text: '默认布置暂未同步到云端（网络或未登录），当前布置仅保存在本机' });
          });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setLoadError(`云端布置读取失败：${msg}。已回退到本机缓存/默认布置。`);
        setServerVersion(0);
      });
    return () => {
      cancelled = true;
    };
  }, [view, config.house]);

  /** 统一的草稿更新入口：标脏 + 体积护栏。 */
  const mutate = useCallback((next: InteriorLayout) => {
    if (isLayoutTooLarge(next)) {
      setSaveState({ kind: 'error', text: '布置体积超过 64KB 上限，未应用本次改动' });
      return;
    }
    setLayout(next);
    setDirty(true);
  }, []);

  const handleAdd = useCallback(
    (furnitureId: string) => {
      if (layout.items.length >= MAX_ITEMS) {
        setSaveState({ kind: 'error', text: `已达家具数量上限（${MAX_ITEMS} 件）` });
        return;
      }
      // 找一个不与现有地板家具同格的落点（夏季house 允许部分重叠，但不刻意压在同一格）。
      const spot = findFreeSpot(layout, furnitureId) ?? { x: 6, y: 5 };
      const next = addItem(layout, furnitureId, spot.x, spot.y);
      if (next === layout) {
        setSaveState({ kind: 'error', text: '这件家具现在放不下（已达上限或未注册）' });
        return;
      }
      const added = next.items[next.items.length - 1]!;
      mutate(next);
      setSelectedId(added.id);
      glowSeq.current += 1;
      setGlowToken({ itemId: added.id, token: glowSeq.current });
      saySeq.current += 1;
      setSayToken({ text: placeFeedback(Math.random), token: saySeq.current });
    },
    [layout, mutate],
  );

  const handleDice = useCallback(() => {
    const next = rollDiceFurniture(layout, { level: cabinLevel });
    if (next === layout) {
      setSaveState({ kind: 'error', text: '骰子没投出新的家具（已达上限或暂无可解锁家具）' });
      return;
    }
    mutate(next);
    const added = next.items[next.items.length - 1];
    if (added) {
      glowSeq.current += 1;
      setGlowToken({ itemId: added.id, token: glowSeq.current });
    }
  }, [layout, cabinLevel, mutate]);

  const handleItemMoved = useCallback(
    (id: string, gx: number, gy: number) => {
      mutate(moveItem(layout, id, gx, gy, snapEnabled));
    },
    [layout, mutate, snapEnabled],
  );

  const handleFurnitureTap = useCallback(
    (item: InteriorItem) => {
      // 更衣镜 → W11 角色工坊（用户需求：更衣镜是换装入口）。
      if (item.furnitureId === 'mirror') {
        navigate('/avatar');
        return;
      }
      if (item.furnitureId === 'stove') {
        saySeq.current += 1;
        setSayToken({ text: '🍳 炉火正旺，可以烹制面包、果酱或热汤。', token: saySeq.current });
        return;
      }
      if (item.furnitureId === 'crate' || item.furnitureId === 'cabinet') {
        saySeq.current += 1;
        setSayToken({ text: '📦 储物箱已整理完毕，可随时存取材料与装备。', token: saySeq.current });
        return;
      }
      const act = resolveFurnitureInteraction(item);
      if (!act) return; // interact='none'：不硬凑演出（诚实失败）
      if (act.kind === 'sleep') {
        setSleepToken((n) => n + 1);
        void executeSleep(handleLifeAction, lifeSnapshot)
          .then((report) => {
            saySeq.current += 1;
            setSayToken({
              text: report.note || `已睡到第 ${report.dayAfter} 天 06:00`,
              token: saySeq.current,
            });
          })
          .catch((err) => {
            setLifeError(err instanceof Error ? err.message : String(err));
          });
        return;
      }
      saySeq.current += 1;
      setSayToken({ text: act.text, token: saySeq.current });
    },
    [navigate, handleLifeAction],
  );

  const handleFloorTap = useCallback((gx: number, gy: number) => {
    personSeq.current += 1;
    setPersonToken({ gx, gy, token: personSeq.current });
  }, []);

  /* ------------------------------------------------------------------ */
  /* W11 · 专属小人注入                                                  */
  /* ------------------------------------------------------------------ */

  /**
   * 小屋专属小人：只有 confirmed 且已勾「设为小屋专属小人」的档案才返回（后端强制）。
   * 404 = 还没有专属小人 —— 这是正常空态，静默回退默认小人即可，**不阻塞小屋**。
   * 其它错误才提示（且只是提示，不影响小屋可用性）。
   */
  const [houseAvatar, setHouseAvatar] = useState<HouseAvatar | null>(null);
  const [houseAvatarError, setHouseAvatarError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getHouseAvatar()
      .then((profile) => {
        if (!cancelled) {
          setHouseAvatar(profile);
          setHouseAvatarError(null);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (isAvatarNotFound(err)) {
          setHouseAvatar(null);
          setHouseAvatarError(null); // 空态不是错误
          return;
        }
        setHouseAvatar(null);
        const code = avatarErrorCode(err);
        setHouseAvatarError(
          `专属小人读取失败（${code ?? '未知错误'}）：已回退默认小人，小屋其余功能不受影响。`,
        );
      });
    return () => { cancelled = true; };
  }, []);

  // 行走两帧 + 调色板：交给 cabinScene 认矩阵，本页不参与角色设计。
  const houseWalkFrames = useMemo(
    () => (houseAvatar ? walkFrames(houseAvatar.layers).map((f) => f.matrix) : undefined),
    [houseAvatar],
  );
  const housePalette = useMemo(
    () => (houseAvatar ? toPixelPalette(houseAvatar.char_palette) : undefined),
    [houseAvatar],
  );

  const handleSave = useCallback(() => {
    const houseId = config.house;
    const localOk = saveLocalLayout(layout);
    void saveInterior(houseId, layout, serverVersion)
      .then((res) => {
        setServerVersion(res.version);
        setLayout((prev) => ({ ...prev, version: res.version }));
        setDirty(false);
        setSaveState({ kind: 'saved', text: `已保存到云端（版本 ${res.version}）` });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        if (localOk) {
          setSaveState({ kind: 'error', text: `云端保存失败：${msg}。改动已存在本机，但未同步到云端` });
        } else {
          setSaveState({ kind: 'error', text: `云端保存失败：${msg}；且本机存储不可用，本次改动仅在当前会话有效` });
        }
        // 诚实：保持 dirty=true，提示用户可重试或恢复默认。
      });
  }, [config.house, layout, serverVersion]);

  const handleReset = useCallback(() => {
    mutate(defaultLayout(config.house));
    setSelectedId(null);
    setSaveState({ kind: 'idle', text: '已恢复默认布置，记得点「保存布置」同步到云端' });
  }, [config.house, mutate]);

  const handleToggleSnap = useCallback((on: boolean) => {
    setSnapEnabled(on);
  }, []);

  const handleFlip = useCallback(
    (id: string) => mutate(flipItem(layout, id)),
    [layout, mutate],
  );
  const handleCycleColorway = useCallback(
    (id: string) => mutate(cycleColorway(layout, id)),
    [layout, mutate],
  );
  const handleLayer = useCallback(
    (id: string, delta: number) => mutate(shiftLayer(layout, id, delta)),
    [layout, mutate],
  );
  const handleRemove = useCallback(
    (id: string) => {
      mutate(removeItem(layout, id));
      setSelectedId((cur) => (cur === id ? null : cur));
    },
    [layout, mutate],
  );
  const handleExitEdit = useCallback(() => {
    setEditMode(false);
    setSelectedId(null);
  }, []);

  const handleExitIndoor = useCallback(() => {
    setView('outdoor');
    setEditMode(false);
    setSelectedId(null);
  }, []);

  /* ------------------------------------------------------------------ */
  /* W2 · 玩法循环与背景探险                                             */
  /* ------------------------------------------------------------------ */

  /**
   * W2 玩法面板与 W1 的室内/室外视图**正交**：它是叠加在当前视图之上的一层，
   * 这样不必改动 W1 的 `view` 状态机（避免破坏既有室内测试），
   * 同时室内外都能进入探险与任务。
   */
  const [gameplayOpen, setGameplayOpen] = useState(false);
  const toggleGameplay = useCallback(() => setGameplayOpen((v) => !v), []);

  /* ------------------------------------------------------------------ */
  /* W9 · 资产库联动：墙面挂画 + BGM（DOM 覆盖层，不碰 cabinScene）      */
  /* ------------------------------------------------------------------ */
  const media = useCabinMedia();
  const [mediaError, setMediaError] = useState('');
  const handleUnmount = useCallback(
    (asset: AssetRecord) => {
      void assetsApi
        .setMount(asset.id, '')
        .then(() => media.reload())
        .catch((err: unknown) =>
          setMediaError(err instanceof Error ? err.message : String(err)),
        );
    },
    [media],
  );

  /** HUD 用的 NPC 行：纯形状转换，字段缺失一律 null（UI 显示「未回传」），不造默认值。 */
  const hudNpcs: CabinNpcRow[] = useMemo(() => npcRowsFromSnapshot(lifeSnapshot), [lifeSnapshot]);


  return (
    <div className="cabin-root" data-testid="cabin-root">
      {view === 'outdoor' ? (
        <CabinStage
          config={config}
          speech={speech}
          onSpeak={handleSpeak}
          personWalkFrames={houseWalkFrames}
          personPalette={housePalette}
        />
      ) : (
        <InteriorStage
          houseId={config.house}
          layout={layout}
          editMode={editMode}
          selectedId={selectedId}
          placeGlowToken={glowToken}
          sleepToken={sleepToken}
          sayToken={sayToken}
          personToken={personToken}
          callbacks={{
            onFurnitureTap: handleFurnitureTap,
            onFloorTap: handleFloorTap,
            onExit: handleExitIndoor,
            onItemMoved: handleItemMoved,
            onItemSelected: setSelectedId,
          }}
        />
      )}

      {/* W9：墙面挂画覆盖层（读资产库元数据渲染成像素风画框） */}
      <CabinWallArt asset={media.wall} />

      {/*
        包 D · 玻璃 HUD 覆盖层（DOM 层，不碰 pixi canvas）。
        与像素层（下方 <CabinHud /> 的 pixel-top-bar / pixel-bottom-bar）职责不重叠：
        像素层管画布内的硬边游戏 HUD，本层管画布外的 NPC / 场景 / 视图操作。
      */}
      <CabinHud
        view={view}
        editMode={editMode}
        onToggleDecorate={() => {
          if (view === 'outdoor') {
            setView('indoor');
            setEditMode(true);
          } else if (editMode) {
            handleExitEdit();
          } else {
            setEditMode(true);
          }
        }}
        onNavigateBack={() => navigate('/private')}
        currentTheme={config.background}
        onSelectTheme={(bg) => update({ background: bg })}
        timeOfDay={config.timeOfDay ?? 'day'}
        onSelectTimeOfDay={(t) => update({ timeOfDay: t })}
        coins={lifeSnapshot?.save?.coins ?? 0}
        dayText={lifeSnapshot?.hud_line ?? '第1天 上午 晴 · 0 金币'}
        snapshot={lifeSnapshot}
        loading={lifeLoading}
        error={lifeError}
        onAction={handleLifeAction}
      />

      <button type="button" className="cabin-back" onClick={() => navigate('/private')}>
        ← 返回私人空间
      </button>

      {/* W2 · 玩法入口（与室内/室外视图正交，叠加显示） */}
      <button
        type="button"
        className={gameplayOpen ? 'cabin-btn primary w2-toggle open' : 'cabin-btn w2-toggle'}
        data-testid="w2-toggle-gameplay"
        aria-expanded={gameplayOpen}
        aria-pressed={gameplayOpen}
        onClick={toggleGameplay}
      >
        {gameplayOpen ? '✕ 收起玩法面板' : '🧭 探险与任务'}
      </button>

      {gameplayOpen && (
        <div className="w2-overlay" data-testid="w2-overlay">
          <div className="w2-experimental-banner" data-testid="w2-experimental-banner">
            <span className="w2-experimental-badge">实验功能</span>
            <span className="w2-experimental-text">
              此面板为 W2 探险实验系统；金币与时间进度统一以主界面新版 HUD 为准。
            </span>
          </div>
          <GameplayPanel
            personality={config.personPersonality}
            personName={config.personName}
            furnitureCount={layout.items.length}
            showCoins={false}
          />
        </div>
      )}

      {view === 'outdoor' ? (
        <>
          <button
            type="button"
            className="cabin-enter-indoor"
            data-testid="cabin-enter-indoor"
            onClick={() => {
              globalTransitionManager.saveOutdoorState({
                playerX: 0,
                playerY: 0,
                cameraX: 0,
                themeId: config.background,
                timeOfDay: config.timeOfDay ?? 'day',
              });
              setView('indoor');
            }}
          >
            🚪 进屋布置
          </button>
          {activeGatherNode && threeSecondCard && (
            <div className="cabin-gather-prompt" data-testid="cabin-gather-prompt">
              <span className="cabin-gather-text" data-testid="cabin-gather-text">
                {threeSecondCard.fullPrompt}
              </span>
              <button
                type="button"
                className="cabin-btn primary cabin-gather-btn"
                data-testid="cabin-gather-btn"
                onClick={() => void handleGather(activeGatherNode)}
              >
                {threeSecondCard.buttonLabel}
              </button>
            </div>
          )}
        </>
      ) : (
        <div className="cabin-indoor-bar">
          <button
            type="button"
            className="cabin-btn"
            data-testid="cabin-exit-indoor"
            onClick={handleExitIndoor}
          >
            🚪 出门回院子
          </button>
          <button
            type="button"
            className={editMode ? 'cabin-btn primary' : 'cabin-btn'}
            data-testid="cabin-toggle-edit"
            aria-pressed={editMode}
            onClick={() => {
              if (editMode) handleExitEdit();
              else setEditMode(true);
            }}
          >
            {editMode ? '退出布置' : '🪑 布置'}
          </button>
          <div className="cabin-room-tabs" data-testid="cabin-room-bar">
            {getHouseRooms(cabinLevel, unlockedRoomIds).map((room) =>
              room.unlocked ? (
                <button
                  key={room.id}
                  type="button"
                  className={currentRoomId === room.id ? 'cabin-btn primary cabin-room-btn' : 'cabin-btn cabin-room-btn'}
                  data-testid={`cabin-room-${room.id}`}
                  onClick={() => handleSwitchRoom(room.id)}
                >
                  {room.label}
                </button>
              ) : (
                <button
                  key={room.id}
                  type="button"
                  className="cabin-btn cabin-room-expand-btn"
                  data-testid={`cabin-room-expand-${room.id}`}
                  title={room.description}
                  onClick={() => handleExpandRoom(room.id)}
                >
                  +扩建{room.label} ({room.expansionCost}金)
                </button>
              ),
            )}
          </div>
          {saveState.text && (
            <span
              className={saveState.kind === 'error' ? 'cabin-save-state error' : 'cabin-save-state'}
              data-testid="cabin-save-state"
              role="status"
            >
              {saveState.text}
            </span>
          )}
        </div>
      )}

      <CabinHudOverlay
        view={view}
        editMode={editMode}
        currentTheme={config.background}
        timeOfDay={config.timeOfDay ?? 'day'}
        npcs={hudNpcs}
        loading={lifeLoading}
        error={lifeError}
        notice={null}
        onSelectTheme={(bg) => update({ background: bg })}
        onSelectTimeOfDay={(t) => update({ timeOfDay: t })}
        onInteract={(npc) => void handleLifeAction('interact', { npc_id: npc.id })}
        onToggleDecorate={() => {
          if (view === 'outdoor') {
            setView('indoor');
            setEditMode(true);
          } else if (editMode) {
            handleExitEdit();
          } else {
            setEditMode(true);
          }
        }}
        onEnterIndoor={() => {
          globalTransitionManager.saveOutdoorState({
            playerX: 0,
            playerY: 0,
            cameraX: 0,
            themeId: config.background,
            timeOfDay: config.timeOfDay ?? 'day',
          });
          setView('indoor');
        }}
        onExitIndoor={handleExitIndoor}
        onOpenGameplay={toggleGameplay}
        gameplayOpen={gameplayOpen}
      >
        {/* 原 .cabin-toolbar 整块搬进右侧 HUD 栏（children）。
            它原本是静态流式元素，在 .cabin-root（fixed + overflow:hidden）里
            永远排在画布之上、压住第一屏画面；功能一个字都不能删（红线二），
            所以搬家而不是隐藏 —— 搬进可滚动右栏后既不挡画面又不丢功能。 */}
        <section className="cabin-toolbar" aria-label="我的小屋设置">

      {/* A6 · UI 像素化重构：顶部状态栏 32px + 底部工具栏 40px (6×32px圆按钮) + 64×64小地图 + 4px像素弹窗 */}
        <div className="cabin-toolbar-title">
          <strong>🏠 我的小屋</strong>
          <span className="cabin-vr-badge" title="本页面的小人与宠物台词均为虚拟演绎内容">
            虚拟演绎
          </span>
          <span
            className="cabin-source-badge"
            data-testid="cabin-dialogue-source"
            title="台词来源：模型生成 = 后端管家 Agent 实时生成；预生成台词池 = 本地静态台词（模型未配置或调用失败时诚实回退）"
          >
            台词来源：{DIALOGUE_SOURCE_LABEL[dialogueSource ?? 'pool']}
          </span>
        </div>

        <div className="cabin-toolbar-body">
          <fieldset className="cabin-group">
            <legend>房屋模板</legend>
            <div className="cabin-chip-row">
              {CABIN_HOUSES.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  className={config.house === h.id ? 'cabin-chip active' : 'cabin-chip'}
                  aria-pressed={config.house === h.id}
                  data-testid={`house-${h.id}`}
                  onClick={() => update({ house: h.id })}
                >
                  {h.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="cabin-group">
            <legend>背景</legend>
            <div className="cabin-chip-row">
              {CABIN_BACKGROUNDS.map((b) => (
                <button
                  key={b.id}
                  type="button"
                  className={config.background === b.id ? 'cabin-chip active' : 'cabin-chip'}
                  aria-pressed={config.background === b.id}
                  data-testid={`bg-${b.id}`}
                  onClick={() => update({ background: b.id })}
                >
                  {b.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="cabin-group">
            <legend>宠物</legend>
            <div className="cabin-chip-row">
              {PET_COLORS.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className={config.petColor === c.id ? 'cabin-swatch active' : 'cabin-swatch'}
                  style={{ backgroundColor: c.hex }}
                  title={c.label}
                  aria-label={`宠物颜色 ${c.label}`}
                  aria-pressed={config.petColor === c.id}
                  data-testid={`pet-color-${c.id}`}
                  onClick={() => update({ petColor: c.id })}
                />
              ))}
              <select
                className="cabin-select"
                aria-label="宠物性格"
                data-testid="pet-personality"
                value={config.petPersonality}
                onChange={(e) => update({ petPersonality: e.target.value as PersonalityId })}
              >
                {PERSONALITIES.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
          </fieldset>

          <fieldset className="cabin-group">
            <legend>小人</legend>
            <div className="cabin-chip-row">
              <input
                className="cabin-input"
                aria-label="小人名字"
                data-testid="person-name"
                value={config.personName}
                maxLength={16}
                placeholder="给小人起个名字"
                autoComplete="off"
                onChange={(e) => update({ personName: e.target.value })}
              />
              <select
                className="cabin-select"
                aria-label="小人性格"
                data-testid="person-personality"
                value={config.personPersonality}
                onChange={(e) => update({ personPersonality: e.target.value as PersonalityId })}
              >
                {PERSONALITIES.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
          </fieldset>
        </div>

        <div className="cabin-note">
          台词来源：{DIALOGUE_SOURCE_LABEL[dialogueSource ?? 'pool']}
          （模型未配置或调用失败时诚实回退本地台词池，绝不伪装模型生成）；
          点击地面移动小人（宠物会跟随），点击小人 / 宠物触发对话。
        </div>

        {view === 'indoor' && (
          <span className="cabin-ni-sr-only" role="status" data-testid="cabin-selection-status">
            {selectedLabel ? `已选中：${selectedLabel}` : '未选中家具'}
          </span>
        )}

        {view === 'indoor' && loadError && (
          <p className="cabin-load-error" data-testid="cabin-load-error" role="alert">
            {loadError}
          </p>
        )}

        {view === 'indoor' && editMode && (
          <InteriorDecoratePanel
            layout={layout}
            selectedId={selectedId}
            cabinLevel={cabinLevel}
            dirty={dirty}
            snapEnabled={snapEnabled}
            onSelect={setSelectedId}
            onAdd={handleAdd}
            onFlip={handleFlip}
            onCycleColorway={handleCycleColorway}
            onLayer={handleLayer}
            onRemove={handleRemove}
            onDice={handleDice}
            onSave={handleSave}
            onReset={handleReset}
            onExitEdit={handleExitEdit}
            onToggleSnap={handleToggleSnap}
          />
        )}

        {view === 'indoor' && !editMode && (
          <p className="cabin-note" data-testid="cabin-indoor-hint">
            点「🚪 出门回院子」回到室外；点「🪑 布置」进入布置模式。
            非布置模式下点家具会有反馈（床→睡觉、书架→报书名、鱼缸→看鱼），
            更衣镜会打开角色工坊。
          </p>
        )}

        {houseAvatarError && (
          <p className="cabin-load-error" data-testid="cabin-avatar-error" role="alert">
            {houseAvatarError}
          </p>
        )}

        {!houseAvatar && !houseAvatarError && (
          <p className="cabin-note" data-testid="cabin-no-house-avatar">
            院子里的还是默认小人。
            <button type="button" className="cabin-btn" onClick={() => navigate('/avatar')}>
              🪞 去角色工坊生成专属小人
            </button>
          </p>
        )}

        {houseAvatar && (
          <p className="cabin-note" data-testid="cabin-house-avatar">
            院子里走动的已是你的专属小人（短码{' '}
            <code>{houseAvatar.fingerprint.slice(0, 8)}</code>）。
            <button type="button" className="cabin-btn" onClick={() => navigate('/avatar')}>
              🪞 去角色工坊调整
            </button>
          </p>
        )}

        {/* ---- W9 资产库联动：墙面挂画 + BGM 设置卡 ---- */}
        <CabinWallArtCard controller={media} onUnmount={handleUnmount} />
        <CabinBgmCard controller={media} />
        {mediaError && (
          <p className="cabin-load-error" data-testid="w9-media-error" role="alert">
            资产库联动失败：{mediaError}
          </p>
        )}
        </section>
      </CabinHudOverlay>
    </div>
  );
}