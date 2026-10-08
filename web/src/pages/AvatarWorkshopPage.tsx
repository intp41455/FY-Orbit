// W11 · 角色工坊页 —— 画像 → 专属像素小人的唯一入口。
//
// 诚实契约（贯穿全页，视觉层改造不得破坏）：
//  1. **逐项同意**：画像每一项都是独立复选框，用户没勾的一律**不提交**。
//  2. **不假装成功**：生成/确认/出卡任何一步失败都显示后端错误码。
//  3. **微调不毁底稿**：任何时候都能「还原 AI 底稿」。
//  4. **零隐私泄露**：分享卡默认**一个徽章都不勾**。
//
// 视觉层（包 D）：
//  - 预览台 `.cabin-ni-stage` 内是 canvas → **不加 backdrop-filter**（像素会糊）；
//  - 玻璃只用在 canvas 外面（`.cabin-ni-share` / `.cabin-ni-asset`）；
//  - 「参数改动即时预览」：本地即时反映 + 明确标注「本地预览（未提交）」。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  SHARE_BADGE_FIELDS,
  avatarErrorCode,
  confirmAvatar,
  createShareCard,
  generateAvatar,
  getMyAvatar,
  previewAvatar,
  isAvatarNotFound,
  type AvatarProfile,
  type AvatarTuning,
  type PortraitInput,
  type ShareBadgeField,
  type ShareCard,
} from '../api/avatar';
import { LineIcon } from '../components/ui/LineIcon';
import {
  buildShareCardPlan,
  idleFrames,
  paintShareCard,
  walkFrames,
} from '../components/avatar/avatarPixels';
import type { AvatarAnimation } from '../components/avatar/AvatarPreview';
import type { AvatarPreviewPackage } from '../api/avatar';
import '../styles/pages/cabin.css';
import {
  AssetWall,
  AvatarStage,
  BadgePicker,
  MetaList,
  ShareCardFrame,
  SliderRow,
  TuningRow,
  type AssetWallItem,
} from '../components/cabinni/AvatarWorkshopUi';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

/* ------------------------------------------------------------------ */
/* 画像字段定义：逐项同意的单位                                          */
/* ------------------------------------------------------------------ */

interface PortraitField {
  key: keyof PortraitInput;
  label: string;
  hint: string;
  placeholder: string;
  maxLength?: number;
}

// P1-5: MBTI 16 合法值白名单（大小写不敏感）
const VALID_MBTI = new Set([
  'ISTJ', 'ISFJ', 'INFJ', 'INTJ',
  'ISTP', 'ISFP', 'INFP', 'INTP',
  'ESTP', 'ESFP', 'ENFP', 'ENTP',
  'ESTJ', 'ESFJ', 'ENFJ', 'ENTJ',
]);

function isValidMBTI(v: string): boolean {
  return VALID_MBTI.has(v.toUpperCase());
}

const PORTRAIT_FIELDS: PortraitField[] = [
  { key: 'mbti', label: 'MBTI 性格', hint: '16 型之一，来自测评模块', placeholder: 'INFJ', maxLength: 4 },
  { key: 'bazi_element', label: '八字五行', hint: '五行主导：木火土金水', placeholder: '木' },
  { key: 'bazi_day_master', label: '日主天干', hint: '甲乙丙丙戊己庚辛壬癸', placeholder: '甲' },
  { key: 'sun_sign', label: '太阳星座', hint: '决定头饰', placeholder: 'leo' },
  { key: 'moon_sign', label: '月亮星座', hint: '决定披风', placeholder: 'pisces' },
  { key: 'asc_sign', label: '上升星座', hint: '决定随身挂饰', placeholder: 'libra' },
  { key: 'name', label: '姓名 / 昵称', hint: '只用于分享卡显示（最多 32 字）', placeholder: '你的昵称', maxLength: 32 },
  { key: 'mood', label: '近期情绪', hint: 'sunny / calm / melancholy', placeholder: 'calm' },
];

const AGE_BANDS = [
  { value: '', label: '不填' },
  { value: 'child', label: '未成年' },
  { value: 'teen', label: '青少年' },
  { value: 'adult', label: '成年' },
  { value: 'senior', label: '银发' },
];

const GENDERS = [
  { value: '', label: '不填' },
  { value: 'female', label: '女' },
  { value: 'male', label: '男' },
  { value: 'other', label: '其他' },
];

/**
 * 微调滑杆的可选值。**必须与后端 avatar_gen.py 的枚举逐字一致**。
 * 交叉校验由后端 tests/unit/test_tuning_options_contract.py 守住。
 */
const TUNING_OPTIONS = {
  hair_style: ['short_neat', 'short_fluffy', 'bob', 'ponytail', 'bun', 'side_swept', 'undercut', 'long_straight', 'long_wavy', 'curly', 'twin_tail', 'braid'],
  hair_tone: ['ink', 'chestnut', 'gold', 'auburn', 'ash', 'rose', 'mint', 'frost'],
  outfit: ['tshirt', 'knit', 'coat', 'robe', 'dress', 'hoodie', 'vest', 'cape'],
  mouth: ['smile', 'flat', 'open_smile', 'small', 'grin'],
  eye: ['sparkle', 'calm', 'sharp', 'gentle', 'dreamy', 'focused'],
} as const;

const TUNING_LABELS: Record<keyof typeof TUNING_OPTIONS, string> = {
  hair_style: '发型',
  hair_tone: '发色',
  outfit: '服装',
  mouth: '表情',
  eye: '眼神',
};

/**
 * 微调项的中文标签 + 色点。
 *
 * ⚠ 发色/服装色点用的是**素材属性色**，不是 UI 状态语义，所以不套九档状态色。
 *   但「当前选中」不能只靠色点区分，因此每个色点都带 `aria-pressed` 与文字标签。
 */
const HAIR_TONE_LABELS: Record<string, { label: string; dot: string }> = {
  ink: { label: '墨黑', dot: '#1e293b' },
  chestnut: { label: '栗棕', dot: '#7c4a2d' },
  gold: { label: '浅金', dot: '#d9a441' },
  auburn: { label: '赤褐', dot: '#9c4a2f' },
  ash: { label: '灰白', dot: '#94a3b8' },
  rose: { label: '玫瑰', dot: '#f3a6b8' },
  mint: { label: '薄荷', dot: '#5eead4' },
  frost: { label: '霜白', dot: '#e2e8f0' },
};

const OUTFIT_LABELS: Record<string, string> = {
  tshirt: 'T恤',
  knit: '针织衫',
  coat: '外套',
  robe: '长袍',
  dress: '连衣裙',
  hoodie: '连帽衫',
  vest: '背心',
  cape: '披风',
};

const MOUTH_LABELS: Record<string, string> = {
  smile: '微笑',
  flat: '平静',
  open_smile: '开口笑',
  small: '小嘴',
  grin: '咧嘴笑',
};

const EYE_LABELS: Record<string, string> = {
  sparkle: '闪亮',
  calm: '平和',
  sharp: '锐利',
  gentle: '温柔',
  dreamy: '梦幻',
  focused: '专注',
};

const HAIR_STYLE_LABELS: Record<string, string> = {
  short_neat: '短发·利落',
  short_fluffy: '短发·蓬松',
  bob: '波波头',
  ponytail: '马尾',
  bun: '丸子头',
  side_swept: '侧分',
  undercut: '短鬓',
  long_straight: '长直发',
  long_wavy: '长卷发',
  curly: '卷发',
  twin_tail: '双马尾',
  braid: '编发',
};

/** 每个微调项的枚举原值 → 中文含义（页面提示行用，不改选项本身）。 */
/** 取某个微调项的中文标签；缺表项时回退原值，不让界面显示 undefined。 */
function tuningLabel(key: keyof typeof TUNING_OPTIONS, value: string): string {
  if (key === 'hair_tone') return HAIR_TONE_LABELS[value]?.label ?? value;
  if (key === 'outfit') return OUTFIT_LABELS[value] ?? value;
  if (key === 'mouth') return MOUTH_LABELS[value] ?? value;
  if (key === 'eye') return EYE_LABELS[value] ?? value;
  return HAIR_STYLE_LABELS[value] ?? value;
}

const TUNING_GLOSS: Record<keyof typeof TUNING_OPTIONS, (v: string) => string> = {
  hair_style: (v) => HAIR_STYLE_LABELS[v] ?? v,
  hair_tone: (v) => HAIR_TONE_LABELS[v]?.label ?? v,
  outfit: (v) => OUTFIT_LABELS[v] ?? v,
  mouth: (v) => MOUTH_LABELS[v] ?? v,
  eye: (v) => EYE_LABELS[v] ?? v,
};

/* ------------------------------------------------------------------ */

type Phase = 'idle' | 'loading' | 'ready' | 'error';

export function AvatarWorkshopPage() {
  const navigate = useNavigate();

  // --- 画像逐项同意 ---
  const [consent, setConsent] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [gender, setGender] = useState('');
  const [ageBand, setAgeBand] = useState('');

  // --- 角色档案 ---
  const [profile, setProfile] = useState<AvatarProfile | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [errorText, setErrorText] = useState('');
  const [errorCode, setErrorCode] = useState('');

  // --- 微调 ---
  const [tuning, setTuning] = useState<AvatarTuning>({});
  const [hueShift, setHueShift] = useState(0);
  const [animation, setAnimation] = useState<AvatarAnimation>('idle');
  // 微调实时预览包（后端 /api/avatar/preview 重算，不落库）。
  // 有了它，改下拉框能立刻看到新长相，不必点「应用微调」。
  const [previewPkg, setPreviewPkg] = useState<AvatarPreviewPackage | null>(null);

  // --- 自评 ---
  const [score, setScore] = useState(7);
  const [note, setNote] = useState('');
  const [isHouseAvatar, setIsHouseAvatar] = useState(true);

  // --- 分享卡 ---
  const [badges, setBadges] = useState<ShareBadgeField[]>([]);
  const [card, setCard] = useState<ShareCard | null>(null);
  const cardCanvasRef = useRef<HTMLCanvasElement | null>(null);

  /* ---------------- 初始加载：已有档案就回填编辑框 ---------------- */
  useEffect(() => {
    let alive = true;
    void (async () => {
      setPhase('loading');
      try {
        const me = await getMyAvatar();
        if (!alive) return;
        const next: Record<string, boolean> = {};
        const vals: Record<string, string> = {};
        for (const [k, v] of Object.entries(me.portrait ?? {})) {
          if (v == null) continue;
          next[k] = true;
          vals[k] = String(v);
        }
        setConsent(next);
        setDraft(vals);
        setGender(String(me.portrait?.gender ?? ''));
        setAgeBand(String(me.portrait?.age_band ?? ''));
        setTuning((me.overrides ?? {}) as AvatarTuning);
        setHueShift(Number((me.overrides as { hue_shift?: number })?.hue_shift ?? 0));
        setScore(me.likeness_score ?? 7);
        setNote(me.likeness_note ?? '');
        setIsHouseAvatar(me.is_house_avatar);
        setProfile(me);
        setPhase('ready');
      } catch (e) {
        if (!alive) return;
        if (isAvatarNotFound(e)) {
          // 空态不是错误：还没生成过，引导用户填画像
          setProfile(null);
          setPhase('idle');
          setErrorText('');
          return;
        }
        setPhase('error');
        setErrorCode(avatarErrorCode(e) ?? '');
        setErrorText(e instanceof Error ? e.message : '读取角色档案失败');
      }
    })();
    return () => { alive = false; };
  }, []);

  /* ---------------- 只提交被勾选的画像项 ---------------- */
  const payloadPortrait = useMemo((): PortraitInput => {
    const out: PortraitInput = {};
    for (const f of PORTRAIT_FIELDS) {
      if (!consent[f.key]) continue;            // 没勾 = 不提交，绝不偷偷带上
      const v = (draft[f.key] ?? '').trim();
      if (v) out[f.key] = v;
    }
    if (gender) out.gender = gender;
    if (ageBand) out.age_band = ageBand;
    return out;
  }, [consent, draft, gender, ageBand]);

  const currentTuning = useCallback((): AvatarTuning => {
    const t: AvatarTuning = { ...tuning };
    if (hueShift !== 0) t.hue_shift = hueShift;
    return t;
  }, [tuning, hueShift]);

  /* ---------------- 生成 ---------------- */
  const runGenerate = useCallback(async () => {
    setPhase('loading');
    setErrorText('');
    setErrorCode('');
    try {
      const res = await generateAvatar(payloadPortrait, currentTuning());
      setProfile(res);
      setPreviewPkg(null);   // 已落库，预览包作废，界面显示的就是生效后的角色
      setPhase('ready');
      setCard(null);
    } catch (e) {
      setPhase('error');
      setErrorCode(avatarErrorCode(e) ?? '');
      setErrorText(e instanceof Error ? e.message : '生成失败');
    }
  }, [payloadPortrait, currentTuning]);

  /* ---------------- 还原 AI 底稿 ---------------- */
  const restoreBase = useCallback(async () => {
    setPhase('loading');
    setErrorText('');
    setErrorCode('');
    try {
      const res = await generateAvatar(payloadPortrait, {});  // 空 overrides = 丢弃微调
      setProfile(res);
      setTuning({});
      setHueShift(0);
      setCard(null);
      setPhase('ready');
    } catch (e) {
      setPhase('error');
      setErrorCode(avatarErrorCode(e) ?? '');
      setErrorText(e instanceof Error ? e.message : '还原失败');
    }
  }, [payloadPortrait]);

  /* ---------------- 确认（自评 + 专属小人） ---------------- */
  const runConfirm = useCallback(async () => {
    if (!profile) return;
    setPhase('loading');
    setErrorText('');
    setErrorCode('');
    try {
      const res = await confirmAvatar({
        expectedVersion: profile.version,
        likenessScore: score,
        likenessNote: note.trim() || null,
        isHouseAvatar,
      });
      setProfile(res);
      setPhase('ready');
    } catch (e) {
      setPhase('error');
      setErrorCode(avatarErrorCode(e) ?? '');
      setErrorText(
        e instanceof Error && isAvatarNotFound(e)
          ? '找不到角色档案（可能已重新生成），请刷新后重试。'
          : e instanceof Error ? e.message : '确认失败',
      );
    }
  }, [profile, score, note, isHouseAvatar]);

  /* ---------------- 分享卡 ---------------- */
  const runShareCard = useCallback(async () => {
    if (!profile) return;
    setPhase('loading');
    setErrorText('');
    setErrorCode('');
    try {
      const res = await createShareCard({
        badges,                                   // 默认空数组 = 零隐私泄露
        displayName: (draft.name ?? '').trim() || null,
        overrides: currentTuning(),
      });
      setCard(res);
      setPhase('ready');
    } catch (e) {
      setPhase('error');
      setErrorCode(avatarErrorCode(e) ?? '');
      setErrorText(e instanceof Error ? e.message : '生成分享卡失败');
    }
  }, [profile, badges, draft.name, currentTuning]);

  /* ---------------- 绘制分享卡到 canvas ---------------- */
  useEffect(() => {
    const canvas = cardCanvasRef.current;
    if (!canvas || !card) return;
    const plan = buildShareCardPlan({
      width: card.width,
      height: card.height,
      matrix: card.avatar.matrix,
      palette: card.avatar.char_palette,
      badges: card.badges,
      caption: card.caption,
      fingerprintShort: card.fingerprint_short,
      baseFingerprintShort: card.base_fingerprint_short,
      tuned: card.tuned,
      brand: card.brand,
      privacyNote: card.privacy_note,
      excludedCount: card.excluded_fields.length,
    });
    canvas.width = plan.width;
    canvas.height = plan.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    paintShareCard(ctx, plan);
  }, [card]);

  const downloadCard = useCallback(() => {
    const canvas = cardCanvasRef.current;
    if (!canvas) return;
    // toDataURL 是本地导出，不上传任何像素（隐私红线）
    const url = canvas.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = url;
    a.download = `my-pixel-avatar-${card?.fingerprint_short ?? 'card'}.png`;
    a.click();
  }, [card]);

  /* ---------------- 派生动画帧 ---------------- */
  // 微调中且后端已回传预览包时用预览包，否则用已保存的档案。
  // 这两个来源的 layers 结构完全一致，AvatarStage 无需区分。
  const previewLayers = previewPkg?.layers ?? profile?.avatar.layers ?? null;

  const idle = useMemo(
    () => (previewLayers ? idleFrames(previewLayers) : null),
    [previewLayers],
  );
  const walk = useMemo(
    () => (previewLayers ? walkFrames(previewLayers) : null),
    [previewLayers],
  );

  /* ---------------- 微调 → 实时预览（不落库） ---------------- */
  // 每次微调变化都重算一次像素。用 ref 记住最新回调，避免把 tuning 对象
  // 放进依赖数组导致每敲一个字符都重建 effect。
  const tuningRef = useRef({ tuning, hueShift });
  tuningRef.current = { tuning, hueShift };
  const portraitRef = useRef(payloadPortrait);
  portraitRef.current = payloadPortrait;

  useEffect(() => {
    if (!profile) return;            // 没有底稿就没法预览
    let alive = true;
    // 微调为空说明用户还没动过，预览台显示的就是已保存档案，没必要请求。
    if (Object.keys(tuning).length === 0 && hueShift === 0) {
      setPreviewPkg(null);
      return;
    }
    const next: AvatarTuning = { ...tuning };
    if (hueShift !== 0) next.hue_shift = hueShift;
    void (async () => {
      try {
        const pkg = await previewAvatar(next);
        if (alive) setPreviewPkg(pkg);
      } catch {
        // 预览失败不打扰用户：预览是辅助，底稿还在，
        // 用户点「应用微调」仍会拿到后端 422/400 的真实提示。
        if (alive) setPreviewPkg(null);
      }
    })();
    return () => { alive = false; };
  }, [profile, tuning, hueShift]);

  const avatar = profile?.avatar ?? null;
  const advisory = profile?.advisory ?? null;

  /** 微调是否已偏离后端已存的底稿（用于「本地预览（未提交）」标注）。 */
  const tuningDirty =
    Object.keys(tuning).length > 0 || hueShift !== 0;

  /** 素材墙：分享卡可选字段 + 已生成卡片，来源真实，不造条目。 */
  const assetItems: AssetWallItem[] = useMemo(() => {
    const items: AssetWallItem[] = [];
    if (card) {
      items.push({
        id: 'share-card',
        name: '像素小人分享卡',
        meta: `${card.width}×${card.height} · 短码 ${card.fingerprint_short}`,
      });
    }
    if (card && card.badges.length > 0) {
      for (const b of card.badges) {
        items.push({ id: `badge-${b.field}`, name: b.label, meta: `徽章 · ${b.value}` });
      }
    }
    return items;
  }, [card]);

  const tuningValue = (key: keyof typeof TUNING_OPTIONS): string =>
    (tuning[key] as string) ?? (avatar ? String(avatar.params[key] ?? '') : '');

  return (
    <BaseBound surface="avatar-workshop">
      <div className="avatar-workshop">
        <div className="page-head">
          <div>
            <h2>角色工坊</h2>
            <p className="muted">
              用你的画像生成独属于自己的像素小人。生成过程<strong>完全本地</strong>，
              不调用任何大模型 —— 同样的画像永远得到同样的角色。
            </p>
          </div>
          <div className="row" style={{ gap: 'var(--ui-s-2)' }}>
            <button type="button" className="ui-btn" onClick={() => navigate('/cabin')}>
              <LineIcon name="cabin" size={16} />
              去小屋
            </button>
          </div>
        </div>

        <div className="cabin-ni-avatar-grid">
          {/* ================= 左：画像与操作 ================= */}
          <section className="cabin-ni-avatar-panel ui-panel">
            <h3>1 · 画像（逐项同意）</h3>
            <p className="cabin-ni-field-hint">
              只勾选你同意使用的项。没勾的不会提交，缺失维度由本地引擎走中性默认，
              并在预览里标注「待补画像」—— 不会假装那是你的真实数据。
            </p>

            {PORTRAIT_FIELDS.map((f) => {
              const isMBTI = f.key === 'mbti';
              const mbtiValue = (draft.mbti ?? '').trim().toUpperCase();
              const mbtiInvalid = isMBTI && mbtiValue && !isValidMBTI(mbtiValue);
              return (
                <div className="cabin-ni-field" key={f.key}>
                  <label className="cabin-ni-field-label" style={{ cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={Boolean(consent[f.key])}
                      style={{ width: 16, height: 16, accentColor: 'var(--ui-sky-600)' }}
                      onChange={(e) =>
                        setConsent((c) => ({ ...c, [f.key]: e.target.checked }))
                      }
                    />
                    <span>{f.label}</span>
                  </label>
                  <input
                    type="text"
                    className={`ui-input${mbtiInvalid ? ' ui-input--error' : ''}`}
                    placeholder={f.placeholder}
                    disabled={!consent[f.key]}
                    aria-label={f.label}
                    value={draft[f.key] ?? ''}
                    maxLength={f.maxLength}
                    onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                    onBlur={isMBTI ? () => {} : undefined}
                  />
                  <span className="cabin-ni-field-hint">
                    {f.hint}{f.maxLength ? `（${(draft[f.key] ?? '').length}/${f.maxLength}）` : ''}
                    {mbtiInvalid && ' · 非 16 型合法值，生成时将按中性默认处理'}
                  </span>
                </div>
              );
            })}

            <div className="cabin-ni-avatar-two">
              <label className="cabin-ni-field">
                <span className="cabin-ni-field-label">性别（可选）</span>
                <select
                  className="ui-select"
                  aria-label="性别（可选）"
                  value={gender}
                  onChange={(e) => setGender(e.target.value)}
                >
                  {GENDERS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
                </select>
              </label>
              <label className="cabin-ni-field">
                <span className="cabin-ni-field-label">年龄档（可选）</span>
                <select
                  className="ui-select"
                  aria-label="年龄档（可选）"
                  value={ageBand}
                  onChange={(e) => setAgeBand(e.target.value)}
                >
                  {AGE_BANDS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
                </select>
              </label>
            </div>
            <p className="cabin-ni-field-hint">
              性别与年龄档会被记录，但<strong>刻意不改变剪影</strong> —— 角色统一为
              1:1.2 Q 版头身比，不按性别或年龄分化。
            </p>

            <div className="row avatar-actions">
              <button
                type="button"
                className="ui-btn ui-btn--primary"
                onClick={() => void runGenerate()}
                disabled={phase === 'loading'}
              >
                <LineIcon name="sparkles" size={16} />
                {phase === 'loading' ? '生成中…' : avatar ? '重新生成' : '生成我的小人'}
              </button>
              {avatar && (
                <button type="button" className="ui-btn" onClick={() => void restoreBase()}>
                  <LineIcon name="refresh" size={16} />
                  还原 AI 底稿
                </button>
              )}
            </div>

            {phase === 'error' && (
              <div className="notice error" role="alert">
                <LineIcon name="alert" size={16} />
                {errorText}
                {errorCode && <span className="avatar-error-code">（错误码 {errorCode}）</span>}
              </div>
            )}
          </section>

          {/* ================= 中：预览 + 微调 ================= */}
          <section className="cabin-ni-avatar-panel ui-panel">
            <h3>2 · 预览</h3>

            {!avatar && (
              <div className="notice info">
                还没有生成角色。填好左侧画像后点「生成我的小人」——
                全过程在本地完成，不上传任何画像数据。
              </div>
            )}

            {avatar && idle && walk && (
              <>
                <AvatarStage
                  frames={animation === 'walk' ? walk : idle}
                  palette={avatar.char_palette}
                  animation={animation}
                  onAnimation={setAnimation}
                  dirty={tuningDirty}
                />

                {advisory && (
                  <div className={advisory.complete ? 'notice info' : 'notice warn'}>
                    <p style={{ margin: 0 }}>
                      <LineIcon name={advisory.complete ? 'check' : 'alert'} size={16} /> {advisory.note}
                    </p>
                    <p className="cabin-ni-field-hint" style={{ margin: 'var(--ui-s-1) 0 0' }}>
                      {advisory.notes}
                    </p>
                    {advisory.age_band_label && (
                      <p className="cabin-ni-field-hint" style={{ margin: 'var(--ui-s-1) 0 0' }}>
                        已记录年龄档：{advisory.age_band_label}（仅记录，不改剪影）
                      </p>
                    )}
                  </div>
                )}

                <MetaList
                  rows={[
                    { label: '参数空间', value: `${avatar.param_space_size.toLocaleString()} 种组合` },
                    { label: '色板', value: `${Object.keys(avatar.palette).length} 色` },
                    { label: '状态', value: profile?.state === 'confirmed' ? '已确认' : '草稿' },
                    { label: '微调', value: avatar.tuned ? '已微调（可一键还原）' : '未微调' },
                  ]}
                />

                {/* 内部凭据默认折叠：短码用于复现/回溯，不是用户关心的信息。 */}
                <details className="avatar-labels">
                  <summary>技术详情（短码，可用于复现）</summary>
                  <ul>
                    <li>呈现短码：<code>{(profile?.params_fingerprint ?? '—').slice(0, 8)}</code></li>
                    <li>底稿短码：<code>{(profile?.fingerprint ?? '—').slice(0, 8)}</code></li>
                  </ul>
                </details>

                <details className="avatar-labels">
                  <summary>查看生成依据（可回溯）</summary>
                  <ul>
                    {Object.entries(profile?.params.sources ?? {}).map(([k, v]) => (
                      <li key={k}><code>{k}</code> ← {v}</li>
                    ))}
                  </ul>
                </details>
              </>
            )}

            {avatar && (
              <>
                <h3>3 · 微调</h3>
                <p className="cabin-ni-field-hint">
                  微调只改变「现在长什么样」，底稿指纹不变，随时可一键还原。
                  改动会立即在左侧预览台看到，无需点「生成」。
                </p>

                {(
                  Object.keys(TUNING_OPTIONS) as (keyof typeof TUNING_OPTIONS)[]
                ).map((key) => (
                  <TuningRow
                    key={key}
                    label={TUNING_LABELS[key]}
                    gloss={`当前：${TUNING_GLOSS[key](tuningValue(key))}`}
                  >
                    {/*
                      显示中文标签，但 <option value> 仍是后端枚举原值 ——
                      用户看的是中文，契约与白名单校验保持不变。
                    */}
                    <select
                      className="ui-select"
                      value={tuningValue(key)}
                      aria-label={TUNING_LABELS[key]}
                      data-testid={`avatar-ni-select-${key}`}
                      onChange={(e) => setTuning((t) => ({ ...t, [key]: e.target.value }))}
                    >
                      {TUNING_OPTIONS[key].map((opt) => (
                        <option key={opt} value={opt}>
                          {tuningLabel(key, opt)}
                        </option>
                      ))}
                    </select>
                  </TuningRow>
                ))}

                {/* 发色色点图例：信息性展示（不可点），中文名 + 原值成对给出。
                    「当前」用文字 + 描边双重标注，不靠颜色单独区分。 */}
                <div className="cabin-ni-field">
                  <span className="cabin-ni-field-label">发色对照</span>
                  <span className="cabin-ni-swatches" data-testid="avatar-ni-hair-tone-legend">
                    {TUNING_OPTIONS.hair_tone.map((v) => {
                      const meta = HAIR_TONE_LABELS[v];
                      const current = tuningValue('hair_tone') === v;
                      return (
                        <span
                          key={v}
                          className="cabin-ni-swatch"
                          aria-current={current ? 'true' : undefined}
                          style={
                            current
                              ? {
                                  /* 同 ProfileDimensions：薄荷高亮环改由令牌派生。
                                     #2dd4bf == --ui-teal-400，渲染与原字面量等价。 */
                                  borderColor: 'color-mix(in srgb, var(--ui-teal-400) 65%, transparent)',
                                  boxShadow: '0 0 0 1px color-mix(in srgb, var(--ui-teal-400) 22%, transparent)',
                                }
                              : undefined
                          }
                        >
                          <span
                            className="cabin-ni-swatch-dot"
                            style={{ background: meta?.dot }}
                            aria-hidden="true"
                          />
                          {meta?.label ?? v}
                          <span className="ui-hint">{v}</span>
                          {current && <span className="ui-badge ui-badge--verifying">当前</span>}
                        </span>
                      );
                    })}
                  </span>
                </div>

                <SliderRow
                  label="色相偏移"
                  value={hueShift}
                  min={-2}
                  max={2}
                  display={String(hueShift)}
                  onChange={setHueShift}
                  testId="avatar-ni-hue"
                />

                <button type="button" className="ui-btn" onClick={() => void runGenerate()}>
                  <LineIcon name="check" size={16} />
                  应用微调（保存到底稿）
                </button>
              </>
            )}
          </section>

          {/* ================= 右：确认、素材墙与分享卡 ================= */}
          <section className="cabin-ni-avatar-panel ui-panel">
            <h3>4 · 像不像自己？</h3>
            <SliderRow
              label="相似度自评"
              value={score}
              min={1}
              max={10}
              display={`${score} / 10`}
              onChange={setScore}
              testId="avatar-ni-likeness"
            />

            <label className="cabin-ni-field">
              <span className="cabin-ni-field-label">一句感想（可留空）</span>
              <textarea
                className="ui-textarea"
                rows={2}
                maxLength={200}
                aria-label="一句感想（可留空）"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="哪里像你，哪里不像？"
              />
            </label>

            <label className="cabin-ni-badge" style={{ minHeight: 'var(--ui-hit-lg)' }}>
              <input
                type="checkbox"
                checked={isHouseAvatar}
                onChange={(e) => setIsHouseAvatar(e.target.checked)}
              />
              <span>设为小屋专属小人（替换默认小人）</span>
            </label>

            <button
              type="button"
              className="ui-btn ui-btn--primary ui-btn--block"
              disabled={!profile || profile.state === 'confirmed' || phase === 'loading'}
              onClick={() => void runConfirm()}
            >
              <LineIcon name="check" size={16} />
              {profile?.state === 'confirmed' ? '已确认' : '确认这个角色'}
            </button>

            <h3>5 · 分享卡</h3>
            <p className="cabin-ni-field-hint">
              <strong>默认一个都不勾</strong> = 零隐私泄露。只有你主动勾选的字段会出现在
              720×960 导出图上；未勾选的具体字段名也不会印在卡上。
            </p>
            <BadgePicker
              fields={SHARE_BADGE_FIELDS}
              checked={badges}
              onToggle={(f) =>
                setBadges((b) => (b.includes(f as ShareBadgeField) ? b.filter((x) => x !== f) : [...b, f as ShareBadgeField]))
              }
            />

            {/* 素材墙：上传创意挂件入口（内容全部来自真实后端返回，不造条目） */}
            <h3>6 · 素材墙</h3>
            <AssetWall
              items={assetItems}
              onPick={(item) => {
                // 素材墙目前只承载分享卡产物；点击即下载对应 PNG。
                if (item.id === 'share-card') downloadCard();
              }}
              emptyHint="生成并确认角色后，出卡素材会出现在这里；现在还没有可挂的素材。"
            />

            <div className="row avatar-actions">
              <button
                type="button"
                className="ui-btn ui-btn--primary"
                disabled={!profile || profile.state !== 'confirmed' || phase === 'loading'}
                onClick={() => void runShareCard()}
              >
                <LineIcon name="download" size={16} />
                {profile?.state === 'confirmed' ? '生成分享卡' : '请先确认角色'}
              </button>
              {card && (
                <button type="button" className="ui-btn" onClick={downloadCard}>
                  <LineIcon name="download" size={16} />
                  下载 PNG
                </button>
              )}
            </div>

            {card && (
              <ShareCardFrame
                canvasRef={cardCanvasRef}
                caption={card.caption}
                shortCode={card.fingerprint_short}
                baseShortCode={card.base_fingerprint_short}
                tuned={card.tuned}
                excludedCount={card.excluded_fields.length}
              />
            )}
          </section>
        </div>

        <p className="muted avatar-foot">
          想看它在屋里走动？
          <Link to="/cabin">去我的小屋 →</Link>
        </p>
      </div>
    </BaseBound>
  );
}