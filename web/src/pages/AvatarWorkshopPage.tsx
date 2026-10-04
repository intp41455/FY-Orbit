// W11 · 角色工坊页 —— 画像 → 专属像素小人的唯一入口。
//
// 诚实契约（贯穿全页）：
//  1. **逐项同意**：画像每一项都是独立复选框，用户没勾的一律**不提交**，
//     绝不偷偷把已有画像全量上传。缺项由后端走中性默认并在 advisory 里标注
//     「待补画像」，本页把这句话原样显示，不改写、不美化。
//  2. **不假装成功**：生成/确认/出卡任何一步失败都显示后端错误码；
//     404 区分「还没生成过」（空态引导）与其它故障。
//  3. **微调不毁底稿**：任何时候都能「还原 AI 底稿」（丢弃 overrides 重生成）。
//  4. **零隐私泄露**：分享卡默认**一个徽章都不勾**，需要用户主动勾选。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  SHARE_BADGE_FIELDS,
  avatarErrorCode,
  confirmAvatar,
  createShareCard,
  generateAvatar,
  getMyAvatar,
  isAvatarNotFound,
  type AvatarProfile,
  type AvatarTuning,
  type PortraitInput,
  type ShareBadgeField,
  type ShareCard,
} from '../api/avatar';
import { AvatarPreview, type AvatarAnimation } from '../components/avatar/AvatarPreview';
import {
  buildShareCardPlan,
  idleFrames,
  paintShareCard,
  walkFrames,
} from '../components/avatar/avatarPixels';

/* ------------------------------------------------------------------ */
/* 画像字段定义：逐项同意的单位                                          */
/* ------------------------------------------------------------------ */

interface PortraitField {
  key: keyof PortraitInput;
  label: string;
  hint: string;
  placeholder: string;
}

const PORTRAIT_FIELDS: PortraitField[] = [
  { key: 'mbti', label: 'MBTI 性格', hint: '16 型之一，来自测评模块', placeholder: 'INFJ' },
  { key: 'bazi_element', label: '八字五行', hint: '五行主导：木火土金水', placeholder: '木' },
  { key: 'bazi_day_master', label: '日主天干', hint: '甲乙丙丁戊己庚辛壬癸', placeholder: '甲' },
  { key: 'sun_sign', label: '太阳星座', hint: '决定头饰', placeholder: 'leo' },
  { key: 'moon_sign', label: '月亮星座', hint: '决定披风', placeholder: 'pisces' },
  { key: 'asc_sign', label: '上升星座', hint: '决定随身挂饰', placeholder: 'libra' },
  { key: 'name', label: '姓名 / 昵称', hint: '只用于分享卡显示', placeholder: '你的昵称' },
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
 * 微调滑杆的可选值。**必须与后端 avatar_gen.py 的枚举逐字一致**（G1-2 修复：
 * 历史上混入了 14 个后端白名单外的非法值——tea/honey/wheat/silver、tunic/jacket/
 * suit/cape_outfit、frown/smirn、round/sleepy/wink/wide——提交会被后端 422 拒，
 * 前端却照常展示，用户调到这些项就卡死）。
 *
 * 交叉校验由 tests/unit/test_tuning_options_contract.py 守住：它直接读本文件并断言
 * 每个选项 ⊆ 对应后端元组，后端改枚举时测试会立刻红，杜绝再次漂移。
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

/* ------------------------------------------------------------------ */
/* 页面                                                                */
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
        // 回填：只把**后端已存的**画像写回输入框，且勾上同意框
        // （这是用户自己上次提交过的数据，不是我们替他勾的）
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
  const idle = useMemo(
    () => (profile ? idleFrames(profile.avatar.layers) : null),
    [profile],
  );
  const walk = useMemo(
    () => (profile ? walkFrames(profile.avatar.layers) : null),
    [profile],
  );

  const avatar = profile?.avatar ?? null;
  const advisory = profile?.advisory ?? null;

  return (
    <div className="avatar-workshop">
      <div className="page-head">
        <div>
          <h2>角色工坊</h2>
          <p className="muted">
            用你的画像生成独属于自己的像素小人。生成过程<strong>完全本地</strong>，
            不调用任何大模型 —— 同样的画像永远得到同样的角色。
          </p>
        </div>
        <div className="row" style={{ gap: '0.5rem' }}>
          <button type="button" className="small ghost" onClick={() => navigate('/cabin')}>
            🏠 去小屋
          </button>
        </div>
      </div>

      <div className="avatar-grid">
        {/* ================= 左：画像与操作 ================= */}
        <section className="card avatar-panel">
          <h3>1 · 画像（逐项同意）</h3>
          <p className="muted avatar-hint">
            只勾选你同意使用的项。没勾的不会提交，缺失维度由本地引擎走中性默认，
            并在预览里标注「待补画像」—— 不会假装那是你的真实数据。
          </p>

          {PORTRAIT_FIELDS.map((f) => (
            <label key={f.key} className="avatar-field">
              <span className="avatar-check">
                <input
                  type="checkbox"
                  checked={Boolean(consent[f.key])}
                  onChange={(e) =>
                    setConsent((c) => ({ ...c, [f.key]: e.target.checked }))
                  }
                />
                <span>{f.label}</span>
              </span>
              <input
                type="text"
                className="avatar-input"
                placeholder={f.placeholder}
                disabled={!consent[f.key]}
                value={draft[f.key] ?? ''}
                onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
              />
              <span className="avatar-field-hint">{f.hint}</span>
            </label>
          ))}

          <div className="avatar-two-col">
            <label className="avatar-field">
              <span className="avatar-check"><span>性别（可选）</span></span>
              <select
                className="avatar-input"
                value={gender}
                onChange={(e) => setGender(e.target.value)}
              >
                {GENDERS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
              </select>
            </label>
            <label className="avatar-field">
              <span className="avatar-check"><span>年龄档（可选）</span></span>
              <select
                className="avatar-input"
                value={ageBand}
                onChange={(e) => setAgeBand(e.target.value)}
              >
                {AGE_BANDS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
              </select>
            </label>
          </div>
          <p className="muted avatar-hint">
            性别与年龄档会被记录，但<strong>刻意不改变剪影</strong> —— 角色统一为
            1:1.2 Q 版头身比，不按性别或年龄分化。
          </p>

          <div className="row avatar-actions">
            <button
              type="button"
              className="primary"
              onClick={() => void runGenerate()}
              disabled={phase === 'loading'}
            >
              {phase === 'loading' ? '生成中…' : avatar ? '重新生成' : '生成我的小人'}
            </button>
            {avatar && (
              <button type="button" className="small ghost" onClick={() => void restoreBase()}>
                ↺ 还原 AI 底稿
              </button>
            )}
          </div>

          {phase === 'error' && (
            <div className="notice error" role="alert">
              {errorText}
              {errorCode && <span className="avatar-error-code">（错误码 {errorCode}）</span>}
            </div>
          )}
        </section>

        {/* ================= 中：大图预览 ================= */}
        <section className="card avatar-panel avatar-stage-panel">
          <h3>2 · 预览</h3>

          {!avatar && (
            <div className="notice info">
              还没有生成角色。填好左侧画像后点「生成我的小人」——
              全过程在本地完成，不上传任何画像数据。
            </div>
          )}

          {avatar && idle && walk && (
            <>
              <div className="avatar-stage">
                <AvatarPreview
                  frames={animation === 'walk' ? walk : idle}
                  palette={avatar.char_palette}
                  animation={animation}
                  scale={8}
                />
              </div>

              <div className="row avatar-anim-switch" role="group" aria-label="动画">
                {(['idle', 'walk', 'static'] as const).map((a) => (
                  <button
                    key={a}
                    type="button"
                    className={`small ${animation === a ? 'primary' : 'ghost'}`}
                    onClick={() => setAnimation(a)}
                  >
                    {a === 'idle' ? '待机呼吸' : a === 'walk' ? '行走' : '静止'}
                  </button>
                ))}
              </div>

              {advisory && (
                <div className={advisory.complete ? 'notice info' : 'notice warn'}>
                  <p style={{ margin: 0 }}>{advisory.note}</p>
                  <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.82rem' }}>
                    {advisory.notes}
                  </p>
                  {advisory.age_band_label && (
                    <p className="muted" style={{ margin: '0.25rem 0 0', fontSize: '0.82rem' }}>
                      已记录年龄档：{advisory.age_band_label}（仅记录，不改剪影）
                    </p>
                  )}
                </div>
              )}

              <dl className="avatar-meta">
                <div><dt>参数空间</dt><dd>{avatar.param_space_size.toLocaleString()} 种组合</dd></div>
                <div><dt>呈现短码</dt><dd><code>{(profile?.params_fingerprint ?? '—').slice(0, 8)}</code></dd></div>
                <div><dt>底稿短码</dt><dd><code>{(profile?.fingerprint ?? '—').slice(0, 8)}</code></dd></div>
                <div><dt>色板</dt><dd>{Object.keys(avatar.palette).length} 色</dd></div>
                <div><dt>状态</dt><dd>{profile?.state === 'confirmed' ? '已确认' : '草稿'}</dd></div>
                <div><dt>微调</dt><dd>{avatar.tuned ? '已微调（可一键还原）' : '未微调'}</dd></div>
              </dl>

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
              <p className="muted avatar-hint">
                微调只改变「现在长什么样」，底稿指纹不变，随时可一键还原。
              </p>
              {(Object.keys(TUNING_OPTIONS) as (keyof typeof TUNING_OPTIONS)[]).map((key) => (
                <label key={key} className="avatar-field avatar-tuning">
                  <span className="avatar-check"><span>{TUNING_LABELS[key]}</span></span>
                  <select
                    className="avatar-input"
                    value={(tuning[key] as string) ?? avatar.params[key]}
                    onChange={(e) => setTuning((t) => ({ ...t, [key]: e.target.value }))}
                  >
                    {TUNING_OPTIONS[key].map((opt) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                </label>
              ))}
              <label className="avatar-field avatar-tuning">
                <span className="avatar-check"><span>色相偏移</span></span>
                <input
                  type="range"
                  min={-2}
                  max={2}
                  step={1}
                  value={hueShift}
                  onChange={(e) => setHueShift(Number(e.target.value))}
                />
                <span className="avatar-field-hint">{hueShift}</span>
              </label>
              <button type="button" className="small" onClick={() => void runGenerate()}>
                应用微调
              </button>
            </>
          )}
        </section>

        {/* ================= 右：确认与分享卡 ================= */}
        <section className="card avatar-panel">
          <h3>4 · 像不像自己？</h3>
          <label className="avatar-field">
            <span className="avatar-check">
              <span>相似度自评：<strong>{score}</strong> / 10</span>
            </span>
            <input
              type="range"
              min={1}
              max={10}
              step={1}
              value={score}
              onChange={(e) => setScore(Number(e.target.value))}
            />
          </label>
          <label className="avatar-field">
            <span className="avatar-check"><span>一句感想（可留空）</span></span>
            <textarea
              className="avatar-input"
              rows={2}
              maxLength={200}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="哪里像你，哪里不像？"
            />
          </label>
          <label className="avatar-field avatar-checkline">
            <input
              type="checkbox"
              checked={isHouseAvatar}
              onChange={(e) => setIsHouseAvatar(e.target.checked)}
            />
            <span>设为小屋专属小人（替换默认小人）</span>
          </label>
          <button
            type="button"
            className="primary"
            disabled={!profile || profile.state === 'confirmed' || phase === 'loading'}
            onClick={() => void runConfirm()}
          >
            {profile?.state === 'confirmed' ? '已确认' : '确认这个角色'}
          </button>

          <h3>5 · 分享卡</h3>
          <p className="muted avatar-hint">
            <strong>默认一个都不勾</strong> = 零隐私泄露。只有你主动勾选的字段会出现在
            720×960 导出图上；未勾选的具体字段名也不会印在卡上。
          </p>
          <div className="avatar-badge-list">
            {SHARE_BADGE_FIELDS.map((f) => (
              <label key={f} className="avatar-checkline">
                <input
                  type="checkbox"
                  checked={badges.includes(f)}
                  onChange={(e) =>
                    setBadges((b) => (e.target.checked ? [...b, f] : b.filter((x) => x !== f)))
                  }
                />
                <span>{f}</span>
              </label>
            ))}
          </div>
          <div className="row avatar-actions">
            <button
              type="button"
              className="small"
              disabled={!profile || profile.state !== 'confirmed' || phase === 'loading'}
              onClick={() => void runShareCard()}
            >
              {profile?.state === 'confirmed' ? '生成分享卡' : '请先确认角色'}
            </button>
            {card && (
              <button type="button" className="small primary" onClick={downloadCard}>
                ⬇ 下载 PNG
              </button>
            )}
          </div>

          {card && (
            <>
              <canvas
                ref={cardCanvasRef}
                className="avatar-share-card"
                role="img"
                aria-label="我的像素小人分享卡预览"
              />
              <p className="muted avatar-hint">
                短码 <code>{card.fingerprint_short}</code>
                {card.tuned && <> （微调自底稿 <code>{card.base_fingerprint_short}</code>）</>}
                {' · '}已隐藏 {card.excluded_fields.length} 项未勾选信息
              </p>
            </>
          )}
        </section>
      </div>

      <p className="muted avatar-foot">
        想看它在屋里走动？
        <Link to="/cabin">去我的小屋 →</Link>
      </p>
    </div>
  );
}