import { useState } from 'react';
import type { CabinBackgroundId, TimeOfDay } from '../cabinConfig';
import { getThemedWorld } from '../cabinThemedWorlds';
import { getExclusiveNpcByTheme } from '../cabinNpcSystem';

export interface WorldMapEntry {
  id: CabinBackgroundId;
  name: string;
  badge: string;
  tagline: string;
  lore: string;
  specialty: string[];
  climate: string;
  suggestedTime: TimeOfDay;
}

export const WORLD_MAP_DATA: WorldMapEntry[] = [
  {
    id: 'forest',
    name: '老林子 · 晨雾树屋',
    badge: '治愈森系',
    tagline: '古木参天，薄雾轻笼的静谧避风港',
    lore: '千年雪松与青苔石阶交错，清晨常有发光灵蝶穿梭林间，木屋烟囱升起袅袅炊烟。',
    specialty: ['发光野莓', '彩虹鲑鱼', '百年松果'],
    climate: '清凉湿润 · 负氧离子',
    suggestedTime: 'dawn',
  },
  {
    id: 'garden',
    name: '后花园 · 蔷薇花廊',
    badge: '芬芳浪漫',
    tagline: '蜂蝶翩跹，四季如春的庭院花海',
    lore: '精心修剪的白石小径与爬满藤蔓的拱门，在这里可以细嗅晨露薄荷与金黄向日葵的香气。',
    specialty: ['晨露薄荷', '金黄向日葵', '甜香花蜜'],
    climate: '温润微风 · 阳光和煦',
    suggestedTime: 'day',
  },
  {
    id: 'stream',
    name: '溪水边 · 卵石浅滩',
    badge: '垂钓圣境',
    tagline: '清泉石上流，波光粼粼的微波水域',
    lore: '水声潺潺，溪底五彩卵石清晰可见。微风拂过水面，是沉浸垂钓与冥想的最佳去处。',
    specialty: ['翡翠鲤鱼', '夜光水草', '圆润溪石'],
    climate: '清爽凉润 · 潺潺流水',
    suggestedTime: 'day',
  },
  {
    id: 'field',
    name: '金黄田野 · 麦浪风车',
    badge: '秋收丰饶',
    tagline: '金浪滚滚，晚霞与木风车的低语',
    lore: '辽阔的麦田在夕阳下拉长影子，老旧的木制风车缓缓转动，空气中弥漫着烘烤麦香的甘甜。',
    specialty: ['金南瓜', '黄金麦穗', '甘甜草莓'],
    climate: '暖融干燥 · 丰收晚风',
    suggestedTime: 'dusk',
  },
  {
    id: 'planet',
    name: '科幻星球 · 陨坑星尘',
    badge: '深空幻境',
    tagline: '星云弥漫，环形山间的幽蓝流光',
    lore: '远离尘嚣的外星地表，天幕上悬挂着瑰丽的紫金星环，踩在微弱重力的星尘上仿佛轻盈飞翔。',
    specialty: ['星芒碎片', '反重力浮石', '星核尘埃'],
    climate: '静谧真空 · 幽冷星光',
    suggestedTime: 'night',
  },
  {
    id: 'magic',
    name: '魔法大陆 · 紫晶秘境',
    badge: '奇幻魔法',
    tagline: '符文石阵，散发微光的荧光魔蕈',
    lore: '古老法阵周围生长着五彩斑斓的魔法真菌，空气中漂浮着温和的以太粒子与神秘古卷残页。',
    specialty: ['荧光魔蕈', '紫晶原石', '以太星露'],
    climate: '以太充盈 · 幻光微热',
    suggestedTime: 'night',
  },
  {
    id: 'scifi',
    name: '赛博空间 · 霓虹终端',
    badge: '未来数字',
    tagline: '流光矩阵，高架光缆与数据之海',
    lore: '数码小人在流动的代码河畔漫步，全息投影与金属终端闪烁着秩序与逻辑的幽蓝美学。',
    specialty: ['量子芯片', '光导纤维', '电子火花'],
    climate: '恒温机房 · 微鸣律动',
    suggestedTime: 'night',
  },
  {
    id: 'country',
    name: '田园乡村 · 红瓦农庄',
    badge: '宁静乡村',
    tagline: '白木栅栏，挂满枝头的红苹果',
    lore: '慢节奏的质朴乡村生活，门前有一畦泥土苗圃，闲暇时靠在干草垛旁读一本泛黄的小说。',
    specialty: ['蜜脆苹果', '新鲜土鸡蛋', '干草垛'],
    climate: '舒缓宜人 · 泥土清香',
    suggestedTime: 'day',
  },
  {
    id: 'ink',
    name: '古风水墨 · 苍松远山',
    badge: '东方意境',
    tagline: '远山含黛，碧波荷池与幽幽青竹',
    lore: '水墨丹青中的留白世界。风吹竹叶沙沙作响，几尾红锦鲤在荷叶底下悄然摆尾。',
    specialty: ['清香白莲', '紫竹春笋', '墨玉灵芝'],
    climate: '空蒙烟雨 · 禅意微风',
    suggestedTime: 'dawn',
  },
];

export interface CozyWorldMapSystemProps {
  currentTheme: CabinBackgroundId;
  onSelectTheme: (id: CabinBackgroundId) => void;
  timeOfDay?: TimeOfDay;
  onSelectTimeOfDay?: (time: TimeOfDay) => void;
  onClose: () => void;
}

export function CozyWorldMapSystem({
  currentTheme,
  onSelectTheme,
  timeOfDay = 'day',
  onSelectTimeOfDay,
  onClose,
}: CozyWorldMapSystemProps) {
  const [selectedMapId, setSelectedMapId] = useState<CabinBackgroundId>(currentTheme);
  const [travelMessage, setTravelMessage] = useState<string | null>(null);

  const activeEntry = WORLD_MAP_DATA.find((m) => m.id === selectedMapId) ?? WORLD_MAP_DATA[0];

  const handleTravel = (id: CabinBackgroundId) => {
    onSelectTheme(id);
    const map = WORLD_MAP_DATA.find((m) => m.id === id);
    if (map && onSelectTimeOfDay) {
      // 推荐时段体验
      onSelectTimeOfDay(map.suggestedTime);
    }
    setTravelMessage(`✨ 正在传送至「${map?.name ?? id}」...`);
    setTimeout(() => {
      onClose();
    }, 450);
  };

  return (
    <div className="pixel-modal-backdrop" data-testid="world-map-backdrop" onClick={onClose}>
      <div
        className="pixel-modal world-map-modal"
        data-testid="world-map-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="大世界地图与场景探索"
      >
        <div className="pixel-modal-header">
          <h2 className="pixel-modal-title">🗺️ 大世界场景舆图与探索</h2>
          <button
            type="button"
            className="pixel-modal-close"
            data-testid="world-map-close"
            onClick={onClose}
            aria-label="关闭地图"
          >
            ✕
          </button>
        </div>

        <div className="pixel-modal-body world-map-body">
          {travelMessage && (
            <div className="travel-banner" role="status" data-testid="travel-status">
              {travelMessage}
            </div>
          )}

          {/* 9 大场景卡片网格 */}
          <div className="world-map-grid" data-testid="world-map-grid">
            {WORLD_MAP_DATA.map((entry) => {
              const isCurrent = entry.id === currentTheme;
              const isSelected = entry.id === selectedMapId;
              return (
                <div
                  key={entry.id}
                  className={`world-map-card ${isSelected ? 'selected' : ''} ${isCurrent ? 'current' : ''}`}
                  data-testid={`world-map-card-${entry.id}`}
                  onClick={() => setSelectedMapId(entry.id)}
                >
                  <div className="map-card-top">
                    <span className="map-card-badge">{entry.badge}</span>
                    {isCurrent && <span className="map-current-pill">当前在此</span>}
                  </div>
                  <h4 className="map-card-name">{entry.name}</h4>
                  <p className="map-card-tagline">{entry.tagline}</p>
                  <div className="map-card-climate">🌡️ {entry.climate}</div>
                </div>
              );
            })}
          </div>

          {/* 选中场景的详尽生态与风貌展示 */}
          <div className="world-map-detail" data-testid="world-map-detail">
            <div className="detail-header">
              <h3>{activeEntry.name}</h3>
              <span className="detail-climate">
                {activeEntry.climate} · 当前: {timeOfDay === 'night' ? '夜晚' : timeOfDay === 'dusk' ? '黄昏' : timeOfDay === 'dawn' ? '清晨' : '白天'}
              </span>
            </div>
            <p className="detail-lore">{activeEntry.lore}</p>

            {/* 专属环境特征 */}
            {(() => {
              const tw = getThemedWorld(activeEntry.id);
              if (!tw || !tw.features.length) return null;
              return (
                <div className="detail-features" data-testid="detail-features">
                  <strong>🏛️ 专属环境特征与人文细节：</strong>
                  <div className="features-pills" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '4px' }}>
                    {tw.features.map((f) => (
                      <span
                        key={f.id}
                        className="specialty-pill"
                        data-testid={`feature-${f.id}`}
                        title={`${f.visualDetail}\n(${f.soundOrAtmosphere})`}
                      >
                        {f.icon} {f.name} · {f.tag}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* 特色交互植物与采集物 */}
            {(() => {
              const tw = getThemedWorld(activeEntry.id);
              if (!tw || !tw.interactivePlants.length) return null;
              return (
                <div className="detail-plants" data-testid="detail-plants" style={{ marginTop: '8px' }}>
                  <strong>🌱 特色交互植物与采集物：</strong>
                  <div className="plants-pills" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '4px' }}>
                    {tw.interactivePlants.map((p) => (
                      <span
                        key={p.id}
                        className="specialty-pill"
                        data-testid={`plant-${p.id}`}
                        title={p.description}
                        style={{ borderLeft: '3px solid #4caf50' }}
                      >
                        {p.icon} 可{p.actionLabel}「{p.name}」({p.minutes}分)
                      </span>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* 专属手绘 NPC 驻守 */}
            {(() => {
              const npc = getExclusiveNpcByTheme(activeEntry.id);
              if (!npc) return null;
              return (
                <div className="detail-npc" data-testid="detail-npc" style={{ marginTop: '8px', padding: '6px 8px', background: 'rgba(0,0,0,0.15)', borderRadius: '4px' }}>
                  <strong>👤 驻守专属手绘 NPC：</strong>
                  <span style={{ fontWeight: 'bold', color: '#ffca28' }}>【{npc.title} · {npc.name}】</span>
                  <p style={{ margin: '3px 0 0', fontSize: '12px', opacity: 0.9 }}>{npc.appearanceDescription}</p>
                </div>
              );
            })()}

            <div className="detail-specialties" style={{ marginTop: '8px' }}>
              <strong>🌿 地域特产与生态物种：</strong>
              <div className="specialty-pills">
                {activeEntry.specialty.map((s) => (
                  <span key={s} className="specialty-pill" data-testid={`specialty-${s}`}>
                    ✦ {s}
                  </span>
                ))}
              </div>
            </div>

            <div className="detail-footer">
              {activeEntry.id === currentTheme ? (
                <button type="button" className="pixel-btn disabled" disabled>
                  🏠 你正漫步于此场景
                </button>
              ) : (
                <button
                  type="button"
                  className="pixel-btn primary highlight"
                  data-testid="world-map-teleport-btn"
                  onClick={() => handleTravel(activeEntry.id)}
                >
                  🚀 启程传送至 {activeEntry.name}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
