/**
 * 品牌命名单一事实源（single source of truth）
 *
 * 依据 deliverables/product-strategy/branding-naming-2026-10-06.md：
 *   - 主标语首词取「本地」（陛下 2026-10-06 拍板，不用「离线」）
 *   - 英文名 FY = Find Yourself 保留为母品牌（文档结论：更换会丢掉「自我认知」
 *     这条线的情感资产，且 FY 已用于品牌与社媒，不建议更换）
 *   - 双线统一叙事：把 FY 当开放前缀「Find Your…」——
 *       自我认知线 Find Yourself ↔ 平台工具线 Find Your Agents
 *   - 命名规则（§一）：禁止点名任何竞品；产品名不得过抽象，须「功能词 + 品类词」
 *     并配功能副名；标语直白、专业、无修辞
 *
 * 文案为什么要集中在这里：改名前全站有**三套说法并存**——侧栏叫「AI 协作工作台」、
 * 登录页叫「Find Yourself」、预览区叫「单主人自我探索陪伴应用」，同一产品三个名字。
 * 集中后改一次即可全局生效。
 *
 * ⚠️ 冻结契约：`e2e_journey.spec.ts:31` 断言登录页存在可访问名为
 * 「Find Yourself」的 heading，因此 `productEn` 不可改；中文功能名是叠加在其下的。
 */

export const BRAND = {
  /** 字标（侧栏 / 登录页圆形标记）。aria-hidden，由文字承担语义。 */
  mark: 'FY',

  /** 母品牌，英文。冻结契约要求登录页 heading 必须是它。 */
  productEn: 'Find Yourself',

  /** 中文功能名。命名书候选中「一眼懂度」最高且与主标语同源，「搭建台」明确是工具。 */
  productZh: '多智能体搭建台',

  /** 主标语：直陈品类，无修辞（命名书方案 A / 极简版）。 */
  slogan: '本地多智能体搭建与调度平台',

  /** 副标语：三条能力并列，全为陈述句，且避开与主标重复的「本地」。 */
  sloganSub: '可视化搭建 · 代码级自定义 · 无需注册联网',

  /** 英文双线叙事（命名书 §三 推荐解法）。 */
  taglineEn: 'Find yourself. Build your agents.',

  /** 中文功能副名，用于需要更细说明的位置。 */
  descriptorZh: '离线搭建与调度 · 可视化与代码同源',
} as const;

/**
 * 侧栏品牌区的一行式组合：中文功能名 + 主标语。
 * 侧栏宽度有限，这里只放主标语；副标语留给登录页首屏与 index.html。
 */
export const BRAND_LINE = `${BRAND.productZh} · ${BRAND.slogan}`;

/** 供 SEO / 应用商店使用的完整一句话（命名书方案 B，32 字）。 */
export const BRAND_SENTENCE =
  `${BRAND.slogan}：可视化搭建、代码级自定义、无需注册联网。`;
