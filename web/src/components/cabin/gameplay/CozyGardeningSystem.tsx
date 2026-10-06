import React, { useState } from 'react';
import './cozyGameplay.css';

export interface CropDefinition {
  id: string;
  name: string;
  icon: string;
  growthTimeSeconds: number;
  sellPrice: number;
}

export const CROPS: Record<string, CropDefinition> = {
  berry: { id: 'berry', name: '发光野莓', icon: '🫐', growthTimeSeconds: 15, sellPrice: 20 },
  mint: { id: 'mint', name: '晨露薄荷', icon: '🌿', growthTimeSeconds: 25, sellPrice: 35 },
  sunflower: { id: 'sunflower', name: '暖阳向日葵', icon: '🌻', growthTimeSeconds: 40, sellPrice: 55 },
  pumpkin: { id: 'pumpkin', name: '仙境金南瓜', icon: '🎃', growthTimeSeconds: 60, sellPrice: 90 },
};

export interface GardenPlot {
  id: number;
  cropId: string | null;
  plantedAt: number | null;
  watered: boolean;
  stage: 'empty' | 'sprout' | 'growing' | 'mature';
}

export interface CozyGardeningProps {
  onHarvest: (crop: CropDefinition) => void;
  onClose: () => void;
}

export const CozyGardeningSystem: React.FC<CozyGardeningProps> = ({ onHarvest, onClose }) => {
  const [plots, setPlots] = useState<GardenPlot[]>([
    { id: 1, cropId: 'berry', plantedAt: Date.now() - 20000, watered: true, stage: 'mature' },
    { id: 2, cropId: 'mint', plantedAt: Date.now() - 10000, watered: true, stage: 'growing' },
    { id: 3, cropId: null, plantedAt: null, watered: false, stage: 'empty' },
    { id: 4, cropId: null, plantedAt: null, watered: false, stage: 'empty' },
  ]);
  const [selectedSeed, setSelectedSeed] = useState<string>('berry');

  const handlePlant = (plotId: number) => {
    setPlots((prev) =>
      prev.map((p) => {
        if (p.id === plotId && p.stage === 'empty') {
          return {
            ...p,
            cropId: selectedSeed,
            plantedAt: Date.now(),
            watered: true,
            stage: 'sprout',
          };
        }
        return p;
      })
    );
  };

  const handleWater = (plotId: number) => {
    setPlots((prev) =>
      prev.map((p) => (p.id === plotId ? { ...p, watered: true } : p))
    );
  };

  const handleHarvest = (plotId: number) => {
    const target = plots.find((p) => p.id === plotId);
    if (!target || target.stage !== 'mature' || !target.cropId) return;

    const def = CROPS[target.cropId];
    if (def) {
      onHarvest(def);
    }

    setPlots((prev) =>
      prev.map((p) =>
        p.id === plotId
          ? { id: p.id, cropId: null, plantedAt: null, watered: false, stage: 'empty' }
          : p
      )
    );
  };

  const handleWaterAll = () => {
    setPlots((prev) =>
      prev.map((p) => (p.stage !== 'empty' ? { ...p, watered: true, stage: 'mature' } : p))
    );
  };

  return (
    <div className="cozy-gardening-modal" data-testid="cozy-gardening-modal">
      <div className="cozy-gardening-card">
        <div className="cozy-modal-header">
          <h3>🌱 庭院耕种与园艺</h3>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <button
              type="button"
              className="cozy-action-btn primary small"
              data-testid="gardening-water-all"
              onClick={handleWaterAll}
            >
              💧 一键甘霖
            </button>
            <button type="button" className="cozy-close-btn" data-testid="cozy-gardening-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="cozy-seed-picker">
          <span className="picker-title">选择手持种子：</span>
          <div className="seed-buttons">
            {Object.values(CROPS).map((crop) => (
              <button
                key={crop.id}
                type="button"
                className={`seed-chip ${selectedSeed === crop.id ? 'active' : ''}`}
                onClick={() => setSelectedSeed(crop.id)}
              >
                {crop.icon} {crop.name}
              </button>
            ))}
          </div>
        </div>

        <div className="cozy-plot-grid">
          {plots.map((plot) => {
            const crop = plot.cropId ? CROPS[plot.cropId] : null;
            return (
              <div key={plot.id} className={`cozy-plot-tile plot-${plot.stage}`} data-testid={`gardening-plot-${plot.id}`}>
                <div className="plot-soil">
                  {plot.stage === 'empty' && (
                    <div className="plot-empty-prompt" onClick={() => handlePlant(plot.id)}>
                      <span>🟫 肥沃泥土</span>
                      <small>点击播种</small>
                    </div>
                  )}

                  {plot.stage === 'sprout' && (
                    <div className="plot-growing-content">
                      <span className="plant-icon animate-pulse">🌱</span>
                      <p>{crop?.name} (幼苗)</p>
                      <button type="button" className="small-action" onClick={() => handleWater(plot.id)}>
                        💧 浇水加速
                      </button>
                    </div>
                  )}

                  {plot.stage === 'growing' && (
                    <div className="plot-growing-content">
                      <span className="plant-icon animate-bounce">🌿</span>
                      <p>{crop?.name} (茁壮中)</p>
                      <button
                        type="button"
                        className="small-action"
                        onClick={() =>
                          setPlots((prev) =>
                            prev.map((p) => (p.id === plot.id ? { ...p, stage: 'mature' } : p))
                          )
                        }
                      >
                        ✨ 施加晨光肥料
                      </button>
                    </div>
                  )}

                  {plot.stage === 'mature' && crop && (
                    <div className="plot-mature-content">
                      <span className="plant-icon mature-glow">{crop.icon}</span>
                      <p className="mature-tag">{crop.name} (已成熟)</p>
                      <button type="button" className="harvest-btn" onClick={() => handleHarvest(plot.id)}>
                        🧺 采摘收获 (+{crop.sellPrice}🪙)
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
