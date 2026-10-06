/**
 * 数码小屋（P2）各各大地图专属环境特征与特色交互植物体系（看板任务 T4.1, T4.2）。
 *
 * 彻底解决“各大地图仅仅更换底图、缺乏周围环境细节、缺乏交互植物与专属特征”的用户诉求。
 *
 * 核心设计：
 * 1. 【老林子 forest】：晨雾古树、红白蘑菇丛、原木花桩、溪泉清风；
 *    - 交互植物：可采摘「灵芝仙菇」「薄荷野草」
 * 2. 【后花园 garden】：白石喷泉、玫瑰花廊、紫藤花架、绿荫石阶、茶歇遮阳伞；
 *    - 交互植物：可采摘「香水大马士革玫瑰」「晨露茉莉」、捕逗「斑斓彩蝶」
 * 3. 【黄金田野 golden_field / field】：旋转大风车、金黄麦浪、稻草垛、南瓜堆；
 *    - 交互植物：可收获「金黄丰收麦穗」「蜜糖大南瓜」
 * 4. 【溪水边 stream】：木质垂钓栈桥、河畔鹅卵石、摇曳芦苇丛、粉白睡莲；
 *    - 交互植物：可采摘「幽香睡莲」「嫩绿水芹」
 * 5. 【观星台 observatory / planet】：古代星盘、黄铜天文望远镜、星座地垫、流星划过；
 *    - 交互道具：可抚触「星象仪」，收集「星芒碎片」
 *
 * 纯逻辑与数据驱动层：完全可在 Node / Vitest 环境测试，并可无缝对接 Pixi 渲染层与 HUD 交互层。
 */

import type { CabinBackgroundId, TimeOfDay } from './cabinConfig';
import type { GatherRow } from './gameplay/lifeApi';
import type { PixelPalette } from './cabinPixels';

/* ------------------------------------------------------------------ */
/* 地图 ID 与规范化映射                                                */
/* ------------------------------------------------------------------ */

export type CanonicalThemeId =
  | 'forest'
  | 'garden'
  | 'golden_field'
  | 'stream'
  | 'observatory';

export type SupportedThemeId =
  | CanonicalThemeId
  | CabinBackgroundId;

/** 将可能带有历史别名的背景 ID 归一化为 5 大核心主题 */
export function normalizeThemeId(themeId: string): CanonicalThemeId {
  switch (themeId) {
    case 'field':
    case 'golden_field':
    case 'country':
      return 'golden_field';
    case 'planet':
    case 'observatory':
    case 'scifi':
      return 'observatory';
    case 'garden':
      return 'garden';
    case 'stream':
      return 'stream';
    case 'forest':
    case 'magic':
    case 'ink':
    default:
      return 'forest';
  }
}

/* ------------------------------------------------------------------ */
/* 专属环境特征体系（Feature）                                         */
/* ------------------------------------------------------------------ */

export type EnvironmentCategory =
  | 'landmark'     // 标志性宏观地标（如：旋转大风车、白石喷泉、古代星盘）
  | 'vegetation'   // 自然植物丛景（如：晨雾古树、紫藤花架、金黄麦浪）
  | 'prop'         // 人文陈设道具（如：茶歇遮阳伞、稻草垛、天文望远镜）
  | 'ambience';    // 氛围流动意象（如：溪泉清风、流星划过、河畔鹅卵石）

export interface EnvironmentalFeature {
  id: string;
  name: string;
  category: EnvironmentCategory;
  icon: string;
  tag: string;
  summary: string;
  visualDetail: string;
  soundOrAtmosphere: string;
  /** 像素美学主色板 */
  colorHighlights: string[];
}

/* ------------------------------------------------------------------ */
/* 特色交互植物 / 采集物体系（Interactive Plant / Prop）              */
/* ------------------------------------------------------------------ */

export type PlantActionType =
  | 'pick'     // 采摘
  | 'harvest'  // 收获 / 收割
  | 'collect'  // 收集
  | 'touch'    // 抚触
  | 'catch';   // 捕逗

export interface InteractivePlantProp {
  id: string;
  name: string;
  type: 'plant' | 'herb' | 'flower' | 'crop' | 'creature' | 'prop';
  icon: string;
  action: PlantActionType;
  actionLabel: string;
  material: string;
  productName: string;
  qtyRange: [number, number];
  minutes: number;
  cooldownSeconds: number;
  description: string;
  lore: string;
  defaultTile: [number, number];
  /** 像素字符画渲染矩阵 */
  pixelArtRows?: readonly string[];
  pixelPalette?: PixelPalette;
}

/* ------------------------------------------------------------------ */
/* 完整主题世界模型（Themed World）                                    */
/* ------------------------------------------------------------------ */

export interface ThemedWorldData {
  id: CanonicalThemeId;
  aliasIds: readonly string[];
  name: string;
  title: string;
  badge: string;
  tagline: string;
  description: string;
  climate: string;
  scenicQuote: string;
  suggestedTime: TimeOfDay;
  /** 四时气氛演色 */
  timeAtmosphere: Record<TimeOfDay, string>;
  /** 专属环境特征清单（至少 4 项，严格对齐用户需求） */
  features: readonly EnvironmentalFeature[];
  /** 特色交互植物 / 道具（严格对齐需求采摘项） */
  interactivePlants: readonly InteractivePlantProp[];
  /** 产物清单 */
  specialties: readonly string[];
  /** 对应专属 NPC ID */
  exclusiveNpcId: string;
  exclusiveNpcName: string;
}

/* ------------------------------------------------------------------ */
/* 5 大地图专属环境特征与交互物完整真源定义                            */
/* ------------------------------------------------------------------ */

export const THEMED_WORLDS: Record<CanonicalThemeId, ThemedWorldData> = {
  /* ================================================================ */
  /* 1. 【老林子 forest】                                             */
  /* ================================================================ */
  forest: {
    id: 'forest',
    aliasIds: ['forest', 'magic', 'ink'],
    name: '老林子 · 晨雾古树',
    title: '幽远清森 · 灵芝泉韵',
    badge: '治愈森系',
    tagline: '古木参天，薄雾轻笼的静谧避风港',
    description:
      '千百年雪松与原木花桩错落有致，晨曦穿透林间薄雾，红白蘑菇丛在树脚悄然绽放，耳畔唯有清泉漱石与松针微响。',
    climate: '清凉湿润 · 负氧离子',
    scenicQuote: '“泉声咽危石，日色冷青松。晨雾初开处，灵芝草木深。”',
    suggestedTime: 'dawn',
    timeAtmosphere: {
      dawn: '薄蓝晨雾缭绕古木，林隙洒下第一缕淡金光束',
      day: '阳光透射绿冠，树影斑驳，空气清甜爽朗',
      dusk: '夕霞浸染松梢，林间浮起微温的紫霭与归鸟啼鸣',
      night: '月光如水泻于青苔，夜间灵菌散发幽幽萤光',
    },
    features: [
      {
        id: 'forest_ancient_tree',
        name: '晨雾古树',
        category: 'vegetation',
        icon: '🌲',
        tag: '千年森魂',
        summary: '巨型古松与云杉，树冠如云覆蔽，晨雾在其繁茂枝叶间流淌。',
        visualDetail: '树干直径需数人合抱，深褐色龟裂树皮上生长着厚软的深绿青苔与微光地衣。',
        soundOrAtmosphere: '微风拂过松针的沙沙声，仿佛古老大地均匀深沉的呼吸。',
        colorHighlights: ['#2e473b', '#5c8368', '#a8c5a0'],
      },
      {
        id: 'forest_mushroom_cluster',
        name: '红白蘑菇丛',
        category: 'vegetation',
        icon: '🍄',
        tag: '林下灵蕈',
        summary: '古树根须旁生机盎然的红盖白斑伞菇群，圆润憨态。',
        visualDetail: '鲜红的伞盖上点缀着白雪般的斑点，小巧的菌柄在湿润落叶堆中挺立，晶莹朝露未晞。',
        soundOrAtmosphere: '泥土与腐殖质特有的芬芳湿润气息。',
        colorHighlights: ['#e53935', '#ffffff', '#fff3e0'],
      },
      {
        id: 'forest_wood_stump',
        name: '原木花桩',
        category: 'prop',
        icon: '🪵',
        tag: '年轮新生',
        summary: '经年风化后自然中空的老松桩，中央开满了嫩黄与粉白野花。',
        visualDetail: '年轮纹理清晰可辨，裂隙处铺着一层毛茸茸的苔藓，宛如大自然亲手雕琢的天然花盆。',
        soundOrAtmosphere: '松脂干透后的温醇木香，偶有林雀驻足歇脚。',
        colorHighlights: ['#795548', '#8d6e63', '#ffee58'],
      },
      {
        id: 'forest_stream_breeze',
        name: '溪泉清风',
        category: 'ambience',
        icon: '🎐',
        tag: '清泉漱石',
        summary: '石隙间汩汩流淌的山林冷泉，拂来沁人心脾的凉润微风。',
        visualDetail: '清澈见底的泉水漫过青石，溅起玉雪般的碎浪，水面漂浮着片片碧绿松针。',
        soundOrAtmosphere: '清脆叮咚的水音，空气清新度与负氧离子达到极致。',
        colorHighlights: ['#4fc3f7', '#b3e5fc', '#e0f7fa'],
      },
    ],
    interactivePlants: [
      {
        id: 'forest_lingzhi',
        name: '灵芝仙菇',
        type: 'herb',
        icon: '🍄',
        action: 'pick',
        actionLabel: '采摘',
        material: 'lingzhi',
        productName: '灵芝仙菇',
        qtyRange: [1, 2],
        minutes: 10,
        cooldownSeconds: 120,
        description: '生于晨雾古树根部的赤红灵芝，汲取天地灵气，菌盖有年轮状金边。',
        lore: '守林老者常言：“朝饮松间露，夕抚灵芝仙。此物蕴含千百年林木精萃，滋神养心。”',
        defaultTile: [18, 52],
      },
      {
        id: 'forest_wild_mint',
        name: '薄荷野草',
        type: 'plant',
        icon: '🌿',
        action: 'harvest',
        actionLabel: '采集',
        material: 'mint_herb',
        productName: '薄荷野草',
        qtyRange: [2, 4],
        minutes: 5,
        cooldownSeconds: 60,
        description: '生于清泉石畔的原生薄荷，叶缘微齿，清香扑鼻，叶面凝聚着晨露。',
        lore: '摘下一片叶子在指间轻揉，清冽凉意瞬间驱散疲惫，是调配凉茶与提神香包的绝佳材料。',
        defaultTile: [42, 60],
      },
    ],
    specialties: ['灵芝仙菇', '薄荷野草', '晨雾松针', '古树青苔'],
    exclusiveNpcId: 'forest_elder_pine',
    exclusiveNpcName: '守林隐士 · 青松老者',
  },

  /* ================================================================ */
  /* 2. 【后花园 garden】                                             */
  /* ================================================================ */
  garden: {
    id: 'garden',
    aliasIds: ['garden'],
    name: '后花园 · 玫瑰花廊',
    title: '芳菲满园 · 喷泉花语',
    badge: '芬芳浪漫',
    tagline: '白石喷泉与紫藤花架，四季如春的诗意庭院',
    description:
      '白石喷泉潺潺溅玉，大马士革玫瑰缠绕花廊，紫藤垂挂如紫霞云锦，绿荫石阶通往茶歇遮阳伞下的静谧午后。',
    climate: '温润微风 · 阳光和煦',
    scenicQuote: '“日长庭院杨花尽，一架蔷薇雪自香。茶歇伞下清风坐，彩蝶翩翩入画廊。”',
    suggestedTime: 'day',
    timeAtmosphere: {
      dawn: '玫瑰花苞上凝满露珠，茉莉幽香随晨风飘散',
      day: '阳光和煦，白石喷泉折射彩虹，斑斓彩蝶穿梭花架',
      dusk: '斜阳为紫藤花廊镀上一层柔金，石阶浮现惬意树影',
      night: '月光如银倾泻在白石喷泉水池，夜茉莉暗香浮动',
    },
    features: [
      {
        id: 'garden_stone_fountain',
        name: '白石喷泉',
        category: 'landmark',
        icon: '⛲',
        tag: '庭院之心',
        summary: '精雕细琢的三层白大理石喷泉，水流如珠帘般层叠滑落。',
        visualDetail: '喷泉水钵边缘雕有莨苕叶饰纹，清澈水花跌入碧蓝底池，偶有彩虹在水雾中隐现。',
        soundOrAtmosphere: '欢快悦耳的水溅声，带来湿润甜美的凉意。',
        colorHighlights: ['#ffffff', '#eceff1', '#81d4fa'],
      },
      {
        id: 'garden_rose_corridor',
        name: '玫瑰花廊',
        category: 'vegetation',
        icon: '🌹',
        tag: '绯红罗曼',
        summary: '由铸铁白色拱门搭建的长廊，攀满了繁复重瓣的绯红与粉白玫瑰。',
        visualDetail: '玫瑰藤蔓交织成遮阴穹顶，花瓣随微风缓缓旋落，在石阶上铺就一条香氛花毯。',
        soundOrAtmosphere: '浓郁深邃的玫瑰精油香气，甜蜜温润。',
        colorHighlights: ['#e91e63', '#f48fb1', '#4caf50'],
      },
      {
        id: 'garden_wisteria_trellis',
        name: '紫藤花架',
        category: 'vegetation',
        icon: '🍇',
        tag: '如瀑紫霞',
        summary: '古木花架上垂挂着成串浅紫与丁香色的紫藤花瀑，浪漫梦幻。',
        visualDetail: '长达半米的花序如云霞悬垂，微风掠过时如紫色风铃般轻轻摇曳起伏。',
        soundOrAtmosphere: '淡淡的豆蔻清香与花蜜甜香交织。',
        colorHighlights: ['#ab47bc', '#ce93d8', '#7b1fa2'],
      },
      {
        id: 'garden_green_stairs',
        name: '绿荫石阶',
        category: 'prop',
        icon: '🪜',
        tag: '青石漫步',
        summary: '依地势而建的石灰岩缓坡台阶，缝隙间钻出嫩绿的酢浆草。',
        visualDetail: '石阶两旁修剪整齐的球形黄杨木与矮矮的灌木丛，投下惬意的林荫。',
        soundOrAtmosphere: '拾级而上的平稳心境与鞋底轻叩石板的脆响。',
        colorHighlights: ['#cfd8dc', '#b0bec5', '#66bb6a'],
      },
      {
        id: 'garden_tea_umbrella',
        name: '茶歇遮阳伞',
        category: 'prop',
        icon: '⛱️',
        tag: '惬意午后',
        summary: '条纹法式遮阳伞下的雕花白铁艺茶桌，备有瓷杯与花草茶壶。',
        visualDetail: '桌上放置着刚采摘的茉莉花茶与三层点心架，座椅覆有柔软的米白亚麻坐垫。',
        soundOrAtmosphere: '红茶与烘焙小饼干的诱人暖香。',
        colorHighlights: ['#fff8e1', '#ffecb3', '#d7ccc8'],
      },
    ],
    interactivePlants: [
      {
        id: 'garden_damask_rose',
        name: '香水大马士革玫瑰',
        type: 'flower',
        icon: '🌹',
        action: 'pick',
        actionLabel: '采摘',
        material: 'damask_rose',
        productName: '香水大马士革玫瑰',
        qtyRange: [1, 3],
        minutes: 8,
        cooldownSeconds: 90,
        description: '玫瑰花廊顶端盛开的极品大马士革玫瑰，花瓣饱满红润，香气馥郁高贵。',
        lore: '花艺精灵艾莉说：“真正的玫瑰从不在喧嚣中盛开，每一片花瓣都浸润着清晨第一缕阳光的私语。”',
        defaultTile: [24, 48],
      },
      {
        id: 'garden_dew_jasmine',
        name: '晨露茉莉',
        type: 'flower',
        icon: '🌼',
        action: 'pick',
        actionLabel: '采摘',
        material: 'dew_jasmine',
        productName: '晨露茉莉',
        qtyRange: [2, 4],
        minutes: 6,
        cooldownSeconds: 70,
        description: '初绽于喷泉水雾旁的白玉茉莉花苞，凝着甘露，香气清雅澄澈。',
        lore: '花语是纯洁与思念。泡在热水里，花瓣会缓缓舒展如雪蝶展翅。',
        defaultTile: [58, 54],
      },
      {
        id: 'garden_butterfly',
        name: '斑斓彩蝶',
        type: 'creature',
        icon: '🦋',
        action: 'collect',
        actionLabel: '捕逗',
        material: 'butterfly_dust',
        productName: '彩蝶灵粉与金羽',
        qtyRange: [1, 2],
        minutes: 5,
        cooldownSeconds: 60,
        description: '在紫藤花架与喷泉水雾间翩跹飞舞的精灵彩蝶，鳞翅在光下反射七彩琉璃光泽。',
        lore: '与它轻轻嬉逗，它会顽皮地在你的指尖落下一抹轻盈闪烁的幻彩荧粉。',
        defaultTile: [36, 42],
      },
    ],
    specialties: ['香水大马士革玫瑰', '晨露茉莉', '斑斓彩蝶', '花廊蜜露'],
    exclusiveNpcId: 'garden_fairy_ellie',
    exclusiveNpcName: '花艺精灵少女 · 洛丽塔/艾莉',
  },

  /* ================================================================ */
  /* 3. 【黄金田野 golden_field / field】                             */
  /* ================================================================ */
  golden_field: {
    id: 'golden_field',
    aliasIds: ['field', 'golden_field', 'country'],
    name: '黄金田野 · 麦浪风车',
    title: '穗香万里 · 丰收乐章',
    badge: '秋收丰饶',
    tagline: '旋转木风车与翻滚麦浪，晚霞与南瓜堆的低语',
    description:
      '四叶风车在暖风中缓缓旋转，无垠麦浪在夕照下涌动金辉。整齐的稻草垛错落田埂，圆滚滚的蜜糖大南瓜堆出丰收喜悦。',
    climate: '暖融干燥 · 丰收晚风',
    scenicQuote: '“麦浪翻金连远野，风车迎爽立斜阳。草垛南瓜堆笑处，农谚一声话岁丰。”',
    suggestedTime: 'dusk',
    timeAtmosphere: {
      dawn: '金麦尖挂着晶亮晨霜，风车在晨曦剪影中缓缓启动',
      day: '麦芒金亮耀眼，热烈的田风裹挟着烘烤麦香奔涌',
      dusk: '晚霞将天地染作焦糖橙金，长长的影子映在草垛与南瓜上',
      night: '秋凉如水，稻草人守护着静谧田埂，蟋蟀长鸣',
    },
    features: [
      {
        id: 'field_windmill',
        name: '旋转大风车',
        category: 'landmark',
        icon: '💨',
        tag: '麦田灯塔',
        summary: '立于高坡之上的木构大风车，帆布风叶随风恒定周旋。',
        visualDetail: '老杉木搭建的风车塔身风化出温暖的棕黄色，传动齿轮发出质朴悠远的吱呀声。',
        soundOrAtmosphere: '风叶划破气流的呼呼声与麦香风浪共鸣。',
        colorHighlights: ['#8d6e63', '#d7ccc8', '#ffb74d'],
      },
      {
        id: 'field_wheat_sea',
        name: '金黄麦浪',
        category: 'vegetation',
        icon: '🌾',
        tag: '大地金辉',
        summary: '绵延至天际的成熟小麦，麦穗沉甸甸低垂，随风如潮起伏。',
        visualDetail: '金灿灿的麦芒在夕阳折射下如同一片耀眼的金色海洋，颗粒饱满圆润。',
        soundOrAtmosphere: '麦穗互相摩擦发出的沙沙干燥脆响，令人心安的谷物香。',
        colorHighlights: ['#ffb300', '#ffa000', '#ffe082'],
      },
      {
        id: 'field_haystack',
        name: '稻草垛',
        category: 'prop',
        icon: '🛖',
        tag: '田埂暖意',
        summary: '农人精心捆扎的圆柱形金黄干草堆，散落在麦田各处。',
        visualDetail: '干草蓬松紧实，上面铺着麻布防潮盖，偶有好奇的田鼠或麻雀在草垛旁跳跃。',
        soundOrAtmosphere: '太阳晒透干草的暖融焦香，靠上去宛如大地温暖的怀抱。',
        colorHighlights: ['#d7ccc8', '#f57f17', '#fff9c4'],
      },
      {
        id: 'field_pumpkin_pile',
        name: '南瓜堆',
        category: 'prop',
        icon: '🎃',
        tag: '硕果累累',
        summary: '堆放在田头木车旁的巨型金橙色蜜糖大南瓜，圆滚诱人。',
        visualDetail: '厚实墨绿的瓜蒂连着金红斑斓的厚重瓜身，外表覆着一层薄薄的白霜保护层。',
        soundOrAtmosphere: '沉甸甸的踏实感，象征着整整一年的劳作与馈赠。',
        colorHighlights: ['#f57c00', '#ff9800', '#4caf50'],
      },
    ],
    interactivePlants: [
      {
        id: 'field_golden_wheat',
        name: '金黄丰收麦穗',
        type: 'crop',
        icon: '🌾',
        action: 'harvest',
        actionLabel: '收割',
        material: 'golden_wheat',
        productName: '金黄丰收麦穗',
        qtyRange: [3, 6],
        minutes: 12,
        cooldownSeconds: 90,
        description: '田野深处颗粒饱满的黄金小麦，指尖轻捻即脱壳出金黄麦粒。',
        lore: '丰收老农常念叨：“麦芒立秋黄，一穗九重香。这是泥土最诚实的回答。”',
        defaultTile: [32, 56],
      },
      {
        id: 'field_sugar_pumpkin',
        name: '蜜糖大南瓜',
        type: 'crop',
        icon: '🎃',
        action: 'harvest',
        actionLabel: '采收',
        material: 'sugar_pumpkin',
        productName: '蜜糖大南瓜',
        qtyRange: [1, 2],
        minutes: 15,
        cooldownSeconds: 150,
        description: '在秋阳下熟透的甜心巨型南瓜，瓜肉金红甜如蜜糖。',
        lore: '既可蒸烤出浓郁南瓜浓汤，也是万圣节制作雕花南瓜灯的不二之选。',
        defaultTile: [50, 62],
      },
    ],
    specialties: ['金黄丰收麦穗', '蜜糖大南瓜', '烘烤麦香粒', '田园干草束'],
    exclusiveNpcId: 'field_scarecrow_farmer',
    exclusiveNpcName: '麦田守望者 · 活泼稻草人 / 丰收老农',
  },

  /* ================================================================ */
  /* 4. 【溪水边 stream】                                             */
  /* ================================================================ */
  stream: {
    id: 'stream',
    aliasIds: ['stream'],
    name: '溪水边 · 卵石浅滩',
    title: '碧波幽潭 · 垂钓客舟',
    badge: '垂钓圣境',
    tagline: '木质栈桥与粉白睡莲，清泉石上流的静心水域',
    description:
      '老杉木栈桥伸入碧波微漾的溪水，五彩鹅卵石铺满浅滩。摇曳芦苇丛掩映着清幽水湾，粉白睡莲在水面上静吐芳华。',
    climate: '清爽凉润 · 潺潺流水',
    scenicQuote: '“移舟泊烟渚，日暮客愁新。清溪深浅处，睡莲带水熏。”',
    suggestedTime: 'day',
    timeAtmosphere: {
      dawn: '水面浮起淡淡白雾，栈桥木纹挂满清润露珠',
      day: '阳光穿透清冽溪底，波光如碎金跃动在鹅卵石上',
      dusk: '斜阳把溪水染成橘红与青碧相间的锦缎，归鸟掠水',
      night: '月印深潭，静水照幽莲，偶有大鱼跃出水面击起银环',
    },
    features: [
      {
        id: 'stream_fishing_dock',
        name: '木质垂钓栈桥',
        category: 'landmark',
        icon: '🪵',
        tag: '客钓平湖',
        summary: '用经久耐水杉木搭建的延伸栈桥，码头边缘拴着一叶扁舟。',
        visualDetail: '木板被水汽浸润呈深青褐色，立柱上生有细小青绿水苔，栈头备有渔翁的竹木马扎。',
        soundOrAtmosphere: '溪浪轻拍木桩的咚咚声，沉静安然。',
        colorHighlights: ['#5d4037', '#795548', '#80cbc4'],
      },
      {
        id: 'stream_river_pebbles',
        name: '河畔鹅卵石',
        category: 'prop',
        icon: '🪨',
        tag: '温润水石',
        summary: '浅水滩涂上铺展的千万枚圆润鹅卵石，五彩斑斓。',
        visualDetail: '流水日夜冲刷让石面滑润光洁如玛瑙玉髓，水浅处踩上去带来透足的冰凉舒爽。',
        soundOrAtmosphere: '踩踏鹅卵石时发出清脆咯啦轻响。',
        colorHighlights: ['#90a4ae', '#b0bec5', '#cfd8dc'],
      },
      {
        id: 'stream_swaying_reeds',
        name: '摇曳芦苇丛',
        category: 'vegetation',
        icon: '🌾',
        tag: '水草依依',
        summary: '水湾背风处密密丛生的长茎芦苇与香蒲，羽白花絮迎风飘荡。',
        visualDetail: '两米多高的绿芦在水流中优雅摇摆，常有白鹭或翠鸟栖息其间窥视水中游鱼。',
        soundOrAtmosphere: '芦花在风中沙沙低吟，水汽迷蒙。',
        colorHighlights: ['#81c784', '#a5d6a7', '#fff9c4'],
      },
      {
        id: 'stream_water_lily',
        name: '粉白睡莲',
        category: 'vegetation',
        icon: '🪷',
        tag: '静水芙蓉',
        summary: '漂浮在平缓回水湾水面上的睡莲群，碧绿圆盘托举着重瓣芙蓉。',
        visualDetail: '花瓣外层粉嫩如云霞，内层洁白胜雪，金黄色花蕊含着晶莹小水珠。',
        soundOrAtmosphere: '冷香幽远，若有若无的清雅水生花香。',
        colorHighlights: ['#f48fb1', '#ffffff', '#fff59d'],
      },
    ],
    interactivePlants: [
      {
        id: 'stream_fragrant_waterlily',
        name: '幽香睡莲',
        type: 'flower',
        icon: '🪷',
        action: 'pick',
        actionLabel: '采摘',
        material: 'water_lily',
        productName: '幽香睡莲',
        qtyRange: [1, 2],
        minutes: 10,
        cooldownSeconds: 100,
        description: '溪湾缓流中盛放的珍品睡莲，日出而舒，花香清幽，能安神定志。',
        lore: '渔隐客说：“静水才能养出这等灵花。心不静，就算走到水边也闻不见这缕暗香。”',
        defaultTile: [20, 68],
      },
      {
        id: 'stream_tender_cress',
        name: '嫩绿水芹',
        type: 'herb',
        icon: '🌱',
        action: 'harvest',
        actionLabel: '采摘',
        material: 'water_cress',
        productName: '嫩绿水芹',
        qtyRange: [2, 5],
        minutes: 7,
        cooldownSeconds: 80,
        description: '生长于鹅卵石缝急流浅滩上的野生水芹，茎脆如玉，清香辛甘。',
        lore: '用山泉水稍微淘洗，与小鱼同煮，就是林间最鲜美的鱼羹配料。',
        defaultTile: [48, 72],
      },
    ],
    specialties: ['幽香睡莲', '嫩绿水芹', '五彩河卵石', '青玉溪鱼'],
    exclusiveNpcId: 'stream_fisherman_hermit',
    exclusiveNpcName: '碧水钓叟 · 渔隐客',
  },

  /* ================================================================ */
  /* 5. 【观星台 observatory / planet】                              */
  /* ================================================================ */
  observatory: {
    id: 'observatory',
    aliasIds: ['planet', 'observatory', 'scifi'],
    name: '观星台 · 古代星盘',
    title: '浩瀚星穹 · 灵曜玄机',
    badge: '深空幻境',
    tagline: '黄铜望远镜与星座地垫，天穹流星划过的心灵秘境',
    description:
      '悬浮于云海之巅的古代黄铜星盘缓缓旋转，折射天文望远镜洞穿浩瀚星云。星座地垫刻画黄道十二宫玄机，流星雨拖着长长光尾划破天幕。',
    climate: '静谧深邃 · 幽冷星辉',
    scenicQuote: '“天阶夜色凉如水，卧看牵牛织女星。星盘轻旋参造化，流光碎影落苍冥。”',
    suggestedTime: 'night',
    timeAtmosphere: {
      dawn: '深蓝天幕泛起微紫红晕，启明星高悬，星轨余韵仍在流转',
      day: '蔚蓝天穹万里无云，黄铜星盘在日光下流淌古朴金属光芒',
      dusk: '晚霞退去，紫金星云渐次显现，第一颗流星掠过天穹',
      night: '银河如瀑贯穿天幕，星盘仪光辉与璀璨群星交相辉映',
    },
    features: [
      {
        id: 'observatory_astrolabe',
        name: '古代星盘',
        category: 'landmark',
        icon: '🧭',
        tag: '天机浑仪',
        summary: '黄铜精铸的巨型天体星盘仪，内外多环按行星周期精密啮合运转。',
        visualDetail: '盘面上刻有二十八宿古星图、经纬黄道线与精致如诗的符文刻度，轴心镶有发光的星陨原石。',
        soundOrAtmosphere: '钟表机械般细微而神圣的微鸣，伴随轻微以太共振。',
        colorHighlights: ['#ffd54f', '#ffb300', '#7e57c2'],
      },
      {
        id: 'observatory_telescope',
        name: '黄铜天文望远镜',
        category: 'prop',
        icon: '🔭',
        tag: '窥天灵镜',
        summary: '架设在观星台凸台之上的复古超长镜筒折射望远镜，通体包铜。',
        visualDetail: '目镜镜片泛着紫蓝色的光学镀膜光泽，手轮调整平稳，能清晰捕捉木星光环与星云丝缕。',
        soundOrAtmosphere: '沉稳厚重的机械手感与探索宇宙的静肃敬畏。',
        colorHighlights: ['#d7ccc8', '#bcaaa4', '#5c6bc0'],
      },
      {
        id: 'observatory_zodiac_mat',
        name: '星座地垫',
        category: 'prop',
        icon: '✨',
        tag: '十二宫仪',
        summary: '铺设在观测台正中的深蓝丝绒圆形星盘地毯，银线刺绣星座图腾。',
        visualDetail: '黄道十二宫图腾由发光银线刺绣而成，站在地垫中央仰望天穹，仿佛能感应自身命盘星位。',
        soundOrAtmosphere: '柔软厚实的触感，带来踏实安定的心灵庇护。',
        colorHighlights: ['#1a237e', '#3949ab', '#e0e0e0'],
      },
      {
        id: 'observatory_shooting_star',
        name: '流星划过',
        category: 'ambience',
        icon: '🌠',
        tag: '愿景流光',
        summary: '深空不时划过的璀璨流星，在夜空拉出绚丽夺目的光痕与星尘。',
        visualDetail: '银白、蔚蓝与淡金色的流星雨穿透星云，照亮观星台大理石立柱，刹那芳华却永驻心田。',
        soundOrAtmosphere: '心灵被浩瀚星空洗礼的纯粹宁静。',
        colorHighlights: ['#80d8ff', '#ffffff', '#b388ff'],
      },
    ],
    interactivePlants: [
      {
        id: 'observatory_armillary',
        name: '古代星象仪',
        type: 'prop',
        icon: '🪐',
        action: 'touch',
        actionLabel: '抚触',
        material: 'astral_resonance',
        productName: '星轨天象共鸣',
        qtyRange: [1, 1],
        minutes: 15,
        cooldownSeconds: 180,
        description: '陈列于黄铜基座上的精密浑天仪，指尖轻触可引动微弱的星辰以太律动。',
        lore: '观星学者塞莱斯特曾说：“星象仪不仅是指引航向的罗盘，更是映照人类内心喜怒哀乐的心灵之镜。”',
        defaultTile: [22, 44],
      },
      {
        id: 'observatory_star_shards',
        name: '星芒碎片',
        type: 'prop',
        icon: '✨',
        action: 'collect',
        actionLabel: '收集',
        material: 'star_shards',
        productName: '星芒碎片',
        qtyRange: [2, 4],
        minutes: 10,
        cooldownSeconds: 120,
        description: '流星划过夜空后坠落在星座地垫边缘的炽热星晶，闪烁着恒定冷光。',
        lore: '携带着来自数亿光年外的远古温度，蕴含着可用于星盘祈愿与工艺附魔的神秘星辉。',
        defaultTile: [46, 50],
      },
    ],
    specialties: ['星芒碎片', '星轨天象共鸣', '流星陨尘', '黄铜星轮'],
    exclusiveNpcId: 'observatory_celeste',
    exclusiveNpcName: '观星学者 · 塞莱斯特',
  },
};

/* ------------------------------------------------------------------ */
/* 检索与格式转换工具函数                                              */
/* ------------------------------------------------------------------ */

/** 获取对应主题世界数据（自动归一化别名） */
export function getThemedWorld(themeId: string): ThemedWorldData {
  const canonical = normalizeThemeId(themeId);
  return THEMED_WORLDS[canonical] ?? THEMED_WORLDS.forest;
}

/** 获取全部 5 大核心主题世界列表 */
export function getAllThemedWorlds(): ThemedWorldData[] {
  return [
    THEMED_WORLDS.forest,
    THEMED_WORLDS.garden,
    THEMED_WORLDS.golden_field,
    THEMED_WORLDS.stream,
    THEMED_WORLDS.observatory,
  ];
}

/** 获取某个主题的所有交互植物/道具列表 */
export function getInteractivePlantsForTheme(themeId: string): readonly InteractivePlantProp[] {
  return getThemedWorld(themeId).interactivePlants;
}

/** 获取某个主题的所有环境特征 */
export function getEnvironmentalFeaturesForTheme(themeId: string): readonly EnvironmentalFeature[] {
  return getThemedWorld(themeId).features;
}

/**
 * 将特色交互物转换为生命模拟系统通用 GatherRow 接口（判据 I1-I4 兼容）
 */
export function convertPlantToGatherRow(
  plant: InteractivePlantProp,
  playerTile: [number, number] = [0, 0],
): GatherRow {
  const dist =
    Math.abs(playerTile[0] - plant.defaultTile[0]) +
    Math.abs(playerTile[1] - plant.defaultTile[1]);

  return {
    id: plant.id,
    label: plant.name,
    action: plant.action,
    action_label: plant.actionLabel,
    animation: `anim_${plant.action}`,
    material: plant.material,
    qty_range: [...plant.qtyRange],
    minutes: plant.minutes,
    tile: [...plant.defaultTile],
    distance: dist,
    in_range: dist <= 2,
  };
}
