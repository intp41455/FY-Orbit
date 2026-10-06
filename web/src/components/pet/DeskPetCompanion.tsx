import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  getHouseAvatar,
  getMyAvatar,
  isAvatarNotFound,
  type AvatarLayers,
  type AvatarCharPalette,
} from '../../api/avatar';
import {
  idleFrames,
  walkFrames,
  type AvatarFrame,
} from '../avatar/avatarPixels';
import {
  PET_FRAMES,
  PET_COLORS,
  colorToCss,
} from '../../pet/petRenderer';
import './deskPetCompanion.css';

export const DESK_PET_STORAGE_KEY = 'fy.deskpet.enabled';

export type CompanionActivity = 'idle' | 'walking' | 'sleepy' | 'stretching' | 'reading';

export interface CompanionProfileData {
  hasAvatar: boolean;
  name: string;
  title: string;
  mbti: string;
  element: string;
  mood: string;
  dayMaster?: string;
  layers?: AvatarLayers;
  charPalette?: AvatarCharPalette;
  labels?: Record<string, string>;
}

const ACTIVITY_LABELS: Record<CompanionActivity, string> = {
  idle: '待机陪伴',
  walking: '工作台踱步',
  sleepy: '小憩打瞌睡',
  stretching: '伸懒腰放松',
  reading: '梳理今日任务',
};

const ACTIVITY_ICONS: Record<CompanionActivity, string> = {
  idle: '✨',
  walking: '🐾',
  sleepy: '💤',
  stretching: '🌱',
  reading: '📖',
};

/**
 * 根据 MBTI + 五行 + 心境 + 当前动作生成符合灵魂特质的专属台词
 */
export function getPersonalityDialogue(
  mbti: string,
  element: string,
  mood: string,
  activity: CompanionActivity,
  hasAvatar: boolean,
): string {
  if (!hasAvatar) {
    if (activity === 'sleepy') {
      return '呼噜呼噜…有点困了喵，去画像测评解锁独一无二专属形象吧~';
    }
    return '去画像测评解锁独一无二专属形象，让我化身你的专属灵魂伴侣！';
  }

  // 动作优先特定台词
  if (activity === 'sleepy') {
    return '呼…眼皮有点沉了，先小憩一会儿。有任何紧急任务随时轻敲我唤醒哦… zZ';
  }
  if (activity === 'stretching') {
    return '（高高伸个懒腰~）啊~ 颈椎和肩膀放松一下，劳逸结合才能保持充沛灵感！';
  }
  if (activity === 'reading') {
    return '正在翻看今日小笔记本，任务脉络正在清晰梳理中，我们节奏超棒！';
  }

  const upperMbti = (mbti || 'INFP').toUpperCase();

  // NT 战略与分析组
  if (upperMbti.includes('NT')) {
    if (mood === 'focused' || activity === 'walking') {
      return '正在梳理今日任务脉络，一切变量与依赖皆在推演规划之中。';
    }
    return '深呼吸，排除低效干扰变量，享受极致专注的高效心流。';
  }

  // NF 理想与共情组
  if (upperMbti.includes('NF')) {
    if (mood === 'calm' || activity === 'walking') {
      return '深呼吸，感受当下的静谧与沉浸心流，灵感正在自然涌现。';
    }
    return '今天也有在认真创造美好的一天呢，心灵的秩序与你的工作一样珍贵。';
  }

  // SJ 秩序与守护组
  if (upperMbti.includes('SJ')) {
    if (activity === 'walking') {
      return '清单上的关键事项正逐一落实，步履不停，稳健前行。';
    }
    return '温水喝了吗？工作台细节已经为你妥善守护，按既定节奏推进即可。';
  }

  // SP 敏锐与探索组
  if (upperMbti.includes('SP')) {
    if (activity === 'walking') {
      return '行动是打破迷雾的最佳武器，随时准备好捕捉新的灵感火花！';
    }
    return '专注每一次键盘敲击的律动，这股心流节奏棒极了！';
  }

  // 五行备选润色
  if (element === '木') return '如春木抽芽，生机盎然；思维正在工作台上自由生长。';
  if (element === '火') return '心怀热忱，如炬如光；灵感之火正熊熊燃烧。';
  if (element === '水') return '上善若水，润物无声；顺应心流，自在变通。';
  if (element === '金') return '明晰利落，秋水澄明；去芜存菁，直击核心要害。';
  if (element === '土') return '厚德载物，心如磐石；踏实稳健是前行最好的底气。';

  return '正在梳理今日任务脉络…深呼吸，享受沉浸心流。';
}

function drawMatrix(
  canvas: HTMLCanvasElement,
  matrix: readonly string[],
  palette: Record<string, string>,
  scale: number,
): void {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const h = matrix.length;
  const w = matrix[0]?.length ?? 0;
  canvas.width = w * scale;
  canvas.height = h * scale;
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  for (let y = 0; y < h; y++) {
    const row = matrix[y];
    for (let x = 0; x < row.length; x++) {
      const ch = row[x];
      if (ch === '.' || ch === ' ') continue;
      const color = palette[ch];
      if (!color) continue;
      ctx.fillStyle = color;
      ctx.fillRect(x * scale, y * scale, scale, scale);
    }
  }
}

export function DeskPetCompanion() {
  const location = useLocation();
  const navigate = useNavigate();

  // 1. 生命周期与场景切换联动：/cabin 路由隐匿，离开平滑回到界面
  const isInsideCabin = location.pathname === '/cabin' || location.pathname.startsWith('/cabin');

  // 2. 开关状态：默认若已完成画像则开启，支持本地存储持久化
  const [enabled, setEnabled] = useState<boolean>(() => {
    const stored = localStorage.getItem(DESK_PET_STORAGE_KEY);
    if (stored !== null) return stored === 'true';
    return true; // 初始允许挂载后根据画像确认
  });

  // 3. 用户专属画像与角色数据
  const [profileData, setProfileData] = useState<CompanionProfileData>({
    hasAvatar: false,
    name: '工作台萌宠',
    title: '温和萌宠 · 陪伴中',
    mbti: 'INFP',
    element: '木',
    mood: 'calm',
  });

  const [isLoading, setIsLoading] = useState(true);
  const [bubbleOpen, setBubbleOpen] = useState(false);
  const [showCard, setShowCard] = useState(false);
  const [activity, setActivity] = useState<CompanionActivity>('idle');
  const [intimacy, setIntimacy] = useState(60);
  const [reactionFx, setReactionFx] = useState<string | null>(null);
  const [customDialogue, setCustomDialogue] = useState<string | null>(null);
  const [frameTick, setFrameTick] = useState(0);
  const [walkOffset, setWalkOffset] = useState(0);
  const [facingRight, setFacingRight] = useState(true);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // 加载画像数据（支持 houseAvatar、getMyAvatar、localStorage 缓存）
  useEffect(() => {
    let cancelled = false;

    async function fetchUserData() {
      setIsLoading(true);
      try {
        // 首选：真实专属小屋小人端点
        const houseAv = await getHouseAvatar().catch((err) => {
          if (isAvatarNotFound(err)) return null;
          return null;
        });

        if (!cancelled && houseAv && houseAv.layers && houseAv.char_palette) {
          const mbti = houseAv.labels?.mbti || 'INFJ';
          const element = houseAv.labels?.element || '木';
          const mood = houseAv.labels?.mood || 'calm';
          setProfileData({
            hasAvatar: true,
            name: `${element}相专属小人`,
            title: `${element}相 · ${mbti} 灵动化身`,
            mbti,
            element,
            mood,
            layers: houseAv.layers,
            charPalette: houseAv.char_palette,
            labels: houseAv.labels,
          });

          // 若此前未手动设开关，拥有画像时默认打开
          if (localStorage.getItem(DESK_PET_STORAGE_KEY) === null) {
            setEnabled(true);
          }
          setIsLoading(false);
          return;
        }

        // 次选：用户个人档案中的 avatar
        const myAv = await getMyAvatar().catch(() => null);
        if (!cancelled && myAv && myAv.avatar && myAv.avatar.layers && myAv.avatar.char_palette) {
          const mbti = (myAv.portrait?.mbti as string) || 'INFJ';
          const element = (myAv.portrait?.bazi_element as string) || '木';
          const mood = (myAv.portrait?.mood as string) || 'calm';
          setProfileData({
            hasAvatar: true,
            name: (myAv.portrait?.name as string) || `${element}相微像素化身`,
            title: `${element}相 · ${mbti} 专属化身`,
            mbti,
            element,
            mood,
            dayMaster: myAv.portrait?.bazi_day_master as string,
            layers: myAv.avatar.layers,
            charPalette: myAv.avatar.char_palette,
            labels: myAv.params?.labels,
          });
          if (localStorage.getItem(DESK_PET_STORAGE_KEY) === null) {
            setEnabled(true);
          }
          setIsLoading(false);
          return;
        }

        // 未生成专属小人：展示温和萌宠状态
        if (!cancelled) {
          setProfileData({
            hasAvatar: false,
            name: '治愈萌宠',
            title: '温和萌宠 · 灵动陪伴',
            mbti: 'INFP',
            element: '木',
            mood: 'calm',
          });
          // 首次未生成画像时，若用户没有显式打开，默认可保持开启并提示测评
          if (localStorage.getItem(DESK_PET_STORAGE_KEY) === null) {
            setEnabled(true);
          }
          setIsLoading(false);
        }
      } catch {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void fetchUserData();

    return () => {
      cancelled = true;
    };
  }, []);

  // 待机呼吸与眨眼帧派生
  const avatarIdleFrames = useMemo<AvatarFrame[]>(() => {
    if (!profileData.layers) return [];
    try {
      return idleFrames(profileData.layers);
    } catch {
      return [];
    }
  }, [profileData.layers]);

  const avatarWalkFrames = useMemo<AvatarFrame[]>(() => {
    if (!profileData.layers) return [];
    try {
      return walkFrames(profileData.layers);
    } catch {
      return [];
    }
  }, [profileData.layers]);

  // 萌宠颜色转 CSS Hex 映射
  const petColorMap = useMemo<Record<string, string>>(() => {
    const map: Record<string, string> = {};
    for (const [k, v] of Object.entries(PET_COLORS)) {
      if (typeof v === 'number') {
        map[k] = colorToCss(v);
      }
    }
    return map;
  }, []);

  // 换帧时钟（每 360ms 轮播）
  useEffect(() => {
    if (isInsideCabin || !enabled) return;
    const interval = window.setInterval(() => {
      setFrameTick((t) => t + 1);
    }, 360);
    return () => window.clearInterval(interval);
  }, [isInsideCabin, enabled]);

  // 自由活动状态机（漫步、踱步、打瞌睡、伸懒腰、翻看小笔记本）
  useEffect(() => {
    if (isInsideCabin || !enabled) return;
    const actTimer = window.setInterval(() => {
      const activities: CompanionActivity[] = ['idle', 'idle', 'walking', 'sleepy', 'stretching', 'reading'];
      const nextAct = activities[Math.floor(Math.random() * activities.length)]!;
      setActivity(nextAct);

      // 如果动作变化且气泡未固定，恢复默认心境台词
      setCustomDialogue(null);

      if (nextAct === 'walking') {
        setFacingRight((prev) => !prev);
      }
    }, 12000);

    return () => window.clearInterval(actTimer);
  }, [isInsideCabin, enabled]);

  // 踱步平移模拟
  useEffect(() => {
    if (activity !== 'walking') return;
    const stepTimer = window.setInterval(() => {
      setWalkOffset((curr) => {
        const next = facingRight ? curr + 4 : curr - 4;
        if (next > 28) {
          setFacingRight(false);
          return 28;
        }
        if (next < -28) {
          setFacingRight(true);
          return -28;
        }
        return next;
      });
    }, 400);

    return () => window.clearInterval(stepTimer);
  }, [activity, facingRight]);

  // 逐帧 Canvas 绘制
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    if (profileData.hasAvatar && profileData.charPalette && avatarIdleFrames.length > 0) {
      // 拥有专属微像素角色
      let currentFrame: AvatarFrame | undefined;
      if (activity === 'walking' && avatarWalkFrames.length > 0) {
        currentFrame = avatarWalkFrames[frameTick % avatarWalkFrames.length];
      } else {
        currentFrame = avatarIdleFrames[frameTick % avatarIdleFrames.length];
      }

      if (currentFrame) {
        drawMatrix(canvas, currentFrame.matrix, profileData.charPalette, 2);
      }
    } else {
      // 温和萌宠状态：使用 PET_FRAMES
      const frameIndex = frameTick % PET_FRAMES.length;
      const petFrame = PET_FRAMES[frameIndex];
      if (petFrame) {
        drawMatrix(canvas, petFrame, petColorMap, 3);
      }
    }
  }, [
    frameTick,
    activity,
    profileData.hasAvatar,
    profileData.charPalette,
    avatarIdleFrames,
    avatarWalkFrames,
    petColorMap,
  ]);

  // 对话气泡文本计算
  const currentDialogue = useMemo(() => {
    if (customDialogue) return customDialogue;
    return getPersonalityDialogue(
      profileData.mbti,
      profileData.element,
      profileData.mood,
      activity,
      profileData.hasAvatar,
    );
  }, [customDialogue, profileData, activity]);

  // 交互 1：点击小人
  const handleActorClick = useCallback(() => {
    setBubbleOpen((prev) => !prev);
    // 每次点开气泡随机微调一次神态
    if (!bubbleOpen) {
      setCustomDialogue(null);
    }
  }, [bubbleOpen]);

  // 交互 2：抚摸
  const handlePetAction = useCallback(() => {
    setIntimacy((prev) => Math.min(100, prev + 2));
    setReactionFx('❤️');
    setTimeout(() => setReactionFx(null), 1400);

    if (profileData.hasAvatar) {
      setCustomDialogue('（眯起双眸舒服地蹭了蹭你的手）好温暖的心流共鸣！谢谢你的鼓励，今天继续加油呀~ ✨');
    } else {
      setCustomDialogue('呼噜呼噜…（舒服地眯起眼睛摇尾巴，亲密度 +2！）');
    }
  }, [profileData.hasAvatar]);

  // 交互 3：戳戳
  const handlePokeAction = useCallback(() => {
    setReactionFx('💡');
    setTimeout(() => setReactionFx(null), 1400);

    if (profileData.hasAvatar) {
      setCustomDialogue('欸？被你轻轻戳中啦！精神抖擞，继续在工作台为你保驾护航！(๑•̀ㅂ•́)و✧');
    } else {
      setCustomDialogue('喵呜！小爪子轻轻抓了抓光标，活力满满地注视着你！');
    }
  }, [profileData.hasAvatar]);

  // 交互 4：查看画像卡片
  const handleToggleProfileCard = useCallback(() => {
    setShowCard((prev) => !prev);
  }, []);

  // 交互 5：进入我的小屋
  const handleEnterCabin = useCallback(() => {
    setBubbleOpen(false);
    navigate('/cabin');
  }, [navigate]);

  // 开关切换：暂时休息 / 唤醒
  const handleToggleEnabled = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      const next = !enabled;
      setEnabled(next);
      localStorage.setItem(DESK_PET_STORAGE_KEY, String(next));
      if (!next) {
        setBubbleOpen(false);
        setShowCard(false);
      }
    },
    [enabled],
  );

  // 当处于 /cabin 路由时，角色返回小屋世界中，组件完全隐匿
  if (isInsideCabin) {
    return null;
  }

  // 当用户选择暂时休息时，展示轻量唤醒胶囊
  if (!enabled) {
    return (
      <div className="desk-pet-container" data-testid="desk-pet-resting">
        <button
          type="button"
          className="desk-pet-resting-pill"
          onClick={handleToggleEnabled}
          title="桌宠当前在休息，点击即可唤醒工作台伴侣"
          aria-label="唤醒工作台桌宠伴侣"
          data-testid="desk-pet-wake-btn"
        >
          <span className="desk-pet-resting-icon" aria-hidden="true">
            💤
          </span>
          <span className="desk-pet-resting-text">桌宠伴侣休息中 · 点击唤醒</span>
        </button>
      </div>
    );
  }

  return (
    <div
      className={`desk-pet-container ${isLoading ? 'loading' : ''}`}
      data-testid="desk-pet-companion"
      style={{
        transform: `translateX(${walkOffset}px)`,
      }}
    >
      <div className="desk-pet-stage">
        {/* 对话气泡 */}
        {bubbleOpen && (
          <div className="desk-pet-bubble" data-testid="desk-pet-bubble" role="dialog" aria-label="桌宠伴侣气泡">
            <div className="desk-pet-bubble-header">
              <div className="desk-pet-title-wrap">
                <span className="desk-pet-name">{profileData.name}</span>
                <span className={`desk-pet-badge ${profileData.hasAvatar ? '' : 'cute'}`}>
                  {profileData.hasAvatar
                    ? `${profileData.element}相 · ${profileData.mbti}`
                    : '萌宠状态'}
                </span>
              </div>
              <div className="desk-pet-actions-header">
                <button
                  type="button"
                  className="desk-pet-toggle-btn"
                  onClick={handleToggleEnabled}
                  title="暂时休息（可随时在右下角唤醒）"
                  aria-label="暂时休息"
                  data-testid="desk-pet-toggle-rest"
                >
                  暂时休息
                </button>
                <button
                  type="button"
                  className="desk-pet-close-btn"
                  onClick={() => setBubbleOpen(false)}
                  title="收起气泡"
                  aria-label="关闭气泡"
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="desk-pet-bubble-body">
              <p className="desk-pet-text" data-testid="desk-pet-text">
                {currentDialogue}
              </p>
              <div className="desk-pet-status-bar">
                <span className="desk-pet-activity-tag">
                  {ACTIVITY_ICONS[activity]} {ACTIVITY_LABELS[activity]}
                </span>
                <span className="desk-pet-intimacy-tag" title="亲密陪伴度">
                  ❤️ 亲密度 {intimacy}
                </span>
              </div>
            </div>

            {/* 详细画像卡片 */}
            {showCard && (
              <div className="desk-pet-card-popover" data-testid="desk-pet-profile-card">
                <div className="desk-pet-card-title">
                  <span>灵魂多维画像档案</span>
                  <span style={{ fontSize: 10, color: '#94a3b8' }}>
                    {profileData.hasAvatar ? '已绑定专属角色' : '未解锁专属角色'}
                  </span>
                </div>
                <div className="desk-pet-card-grid">
                  <div className="desk-pet-card-row">
                    <span>MBTI人格：</span>
                    <strong>{profileData.mbti}</strong>
                  </div>
                  <div className="desk-pet-card-row">
                    <span>五行命理：</span>
                    <strong>{profileData.element}相 {profileData.dayMaster ? `(${profileData.dayMaster})` : ''}</strong>
                  </div>
                  <div className="desk-pet-card-row">
                    <span>当前心境：</span>
                    <strong>{profileData.mood}</strong>
                  </div>
                  <div className="desk-pet-card-row">
                    <span>陪伴称号：</span>
                    <strong>{profileData.title}</strong>
                  </div>
                </div>
                <div className="desk-pet-card-footer">
                  <button
                    type="button"
                    className="desk-pet-card-link"
                    onClick={() => {
                      setBubbleOpen(false);
                      navigate('/avatar');
                    }}
                  >
                    🎨 角色工坊换装
                  </button>
                  <button
                    type="button"
                    className="desk-pet-card-link"
                    onClick={() => {
                      setBubbleOpen(false);
                      navigate('/profiles');
                    }}
                  >
                    📊 多维画像全景
                  </button>
                </div>
              </div>
            )}

            {/* 快捷交互菜单 */}
            <div className="desk-pet-menu" role="menu">
              <button
                type="button"
                className="desk-pet-menu-btn"
                onClick={handlePetAction}
                data-testid="pet-action-pat"
              >
                <span>✋ 抚摸</span>
              </button>
              <button
                type="button"
                className="desk-pet-menu-btn"
                onClick={handlePokeAction}
                data-testid="pet-action-poke"
              >
                <span>👉 戳戳</span>
              </button>
              <button
                type="button"
                className="desk-pet-menu-btn"
                onClick={handleToggleProfileCard}
                data-testid="pet-action-profile"
              >
                <span>🪪 {showCard ? '收起卡片' : '画像卡片'}</span>
              </button>
              {!profileData.hasAvatar ? (
                <button
                  type="button"
                  className="desk-pet-menu-btn"
                  onClick={() => {
                    setBubbleOpen(false);
                    navigate('/assessments');
                  }}
                  data-testid="pet-action-assessment"
                >
                  <span>📝 测评解锁</span>
                </button>
              ) : null}
              <button
                type="button"
                className="desk-pet-menu-btn primary"
                onClick={handleEnterCabin}
                data-testid="pet-action-cabin"
              >
                <span>🏡 进入我的小屋</span>
              </button>
            </div>
          </div>
        )}

        {/* 角色小人实体 */}
        <div
          className={`desk-pet-actor ${activity}`}
          onClick={handleActorClick}
          title={`${profileData.name}（点击互动）`}
          data-testid="desk-pet-actor"
          style={{
            transform: facingRight ? 'none' : 'scaleX(-1)',
          }}
        >
          {reactionFx && (
            <div className="desk-pet-fx" data-testid="desk-pet-fx">
              {reactionFx}
            </div>
          )}

          <canvas
            ref={canvasRef}
            className="desk-pet-canvas"
            data-testid="desk-pet-canvas"
            role="img"
            aria-label={`${profileData.name} - ${ACTIVITY_LABELS[activity]}`}
          />

          <div className="desk-pet-footprint">
            <span className="desk-pet-activity-pill">
              {ACTIVITY_ICONS[activity]} {ACTIVITY_LABELS[activity]}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
export default DeskPetCompanion;
