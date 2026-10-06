import { useState, useEffect, useRef } from 'react';

export interface SoundTrackDef {
  id: string;
  name: string;
  category: string;
  desc: string;
  toneFrequency: number;
}

export const SOUND_TRACKS: SoundTrackDef[] = [
  {
    id: 'breeze',
    name: '🍃 森林微风与落叶',
    category: '自然白噪',
    desc: '温柔穿过松针与山毛榉叶的微风，抚平杂乱的思绪。',
    toneFrequency: 220,
  },
  {
    id: 'rain',
    name: '🌧️ 檐下细雨与青石',
    category: '淅沥雨声',
    desc: '雨滴敲打在原木瓦片与石阶上的清脆声响，适宜深度专注与入眠。',
    toneFrequency: 180,
  },
  {
    id: 'hearth',
    name: '🔥 暖融壁炉与柴火',
    category: '温暖壁炉',
    desc: '干燥松木在壁炉中跳跃劈啪的微响，带着松脂的温暖香气。',
    toneFrequency: 140,
  },
  {
    id: 'brook',
    name: '🌊 卵石山涧与浅溪',
    category: '清澈流水',
    desc: '清澈山泉在光滑卵石间奔流跳跃，带来扑面的湿润凉意。',
    toneFrequency: 260,
  },
  {
    id: 'cicada',
    name: '🦗 夏夜萤火与鸣虫',
    category: '静夜虫鸣',
    desc: '夏末池塘边草丛中细碎的虫鸣，伴随萤火虫的微光呼吸。',
    toneFrequency: 330,
  },
];

export interface CozySoundscapeSystemProps {
  onClose: () => void;
}

export function CozySoundscapeSystem({ onClose }: CozySoundscapeSystemProps) {
  const [selectedTrackIndex, setSelectedTrackIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [volume, setVolume] = useState(60);
  const audioContextRef = useRef<AudioContext | null>(null);
  const oscRef = useRef<OscillatorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);

  const currentTrack = SOUND_TRACKS[selectedTrackIndex] ?? SOUND_TRACKS[0];

  const stopAudio = () => {
    try {
      if (oscRef.current) {
        oscRef.current.stop();
        oscRef.current.disconnect();
        oscRef.current = null;
      }
      if (gainRef.current) {
        gainRef.current.disconnect();
        gainRef.current = null;
      }
    } catch {
      // ignore
    }
  };

  const startAudio = (freq: number, vol: number) => {
    stopAudio();
    if (typeof window === 'undefined') return;
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;

    try {
      if (!audioContextRef.current) {
        audioContextRef.current = new AudioCtx();
      }
      const ctx = audioContextRef.current;
      if (ctx.state === 'suspended') {
        void ctx.resume();
      }

      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, ctx.currentTime);

      gain.gain.setValueAtTime((vol / 100) * 0.15, ctx.currentTime);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start();
      oscRef.current = osc;
      gainRef.current = gain;
    } catch {
      // ignore in mock environments
    }
  };

  useEffect(() => {
    if (isPlaying) {
      startAudio(currentTrack.toneFrequency, volume);
    } else {
      stopAudio();
    }
    return () => {
      stopAudio();
    };
  }, [isPlaying, selectedTrackIndex, currentTrack.toneFrequency, volume]);

  const togglePlay = () => {
    setIsPlaying((prev) => !prev);
  };

  const handleVolumeChange = (newVol: number) => {
    setVolume(newVol);
    if (gainRef.current && audioContextRef.current) {
      gainRef.current.gain.setValueAtTime((newVol / 100) * 0.15, audioContextRef.current.currentTime);
    }
  };

  return (
    <div className="pixel-modal-backdrop" data-testid="soundscape-backdrop" onClick={onClose}>
      <div
        className="pixel-modal soundscape-modal"
        data-testid="soundscape-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="森林留声机与自然声景"
      >
        <div className="pixel-modal-header">
          <h2 className="pixel-modal-title">📻 森林黑胶留声机与治愈自然声景</h2>
          <button
            type="button"
            className="pixel-modal-close"
            data-testid="soundscape-close"
            onClick={onClose}
            aria-label="关闭留声机"
          >
            ✕
          </button>
        </div>

        <div className="pixel-modal-body soundscape-body">
          {/* 黑胶旋转唱片视觉 */}
          <div className="gramophone-display" data-testid="gramophone-display">
            <div className={`vinyl-disc ${isPlaying ? 'spinning' : ''}`} data-testid="vinyl-disc">
              <div className="vinyl-groove" />
              <div className="vinyl-center">🎶</div>
            </div>
            <div className="needle-arm" />
            <div className="now-playing-info">
              <span className="track-badge">{currentTrack.category}</span>
              <h3 className="track-name">{currentTrack.name}</h3>
              <p className="track-desc">{currentTrack.desc}</p>
            </div>
          </div>

          {/* 控制条 */}
          <div className="soundscape-controls">
            <button
              type="button"
              className={`pixel-btn primary highlight play-btn ${isPlaying ? 'playing' : ''}`}
              data-testid="soundscape-play-btn"
              onClick={togglePlay}
            >
              {isPlaying ? '⏸️ 暂停放音' : '▶️ 播放当前声景'}
            </button>

            <div className="volume-control">
              <label htmlFor="soundscape-vol" className="vol-label">
                音量: {volume}%
              </label>
              <input
                id="soundscape-vol"
                type="range"
                min="0"
                max="100"
                value={volume}
                data-testid="soundscape-volume-slider"
                onChange={(e) => handleVolumeChange(Number(e.target.value))}
              />
            </div>
          </div>

          {/* 曲目列表 */}
          <div className="soundscape-track-list" data-testid="soundscape-track-list">
            <h4>🌿 治愈原声带磁带架：</h4>
            <div className="track-grid">
              {SOUND_TRACKS.map((t, idx) => {
                const isSelected = idx === selectedTrackIndex;
                return (
                  <button
                    key={t.id}
                    type="button"
                    className={`track-item-btn ${isSelected ? 'active' : ''}`}
                    data-testid={`track-btn-${t.id}`}
                    onClick={() => {
                      setSelectedTrackIndex(idx);
                      setIsPlaying(true);
                    }}
                  >
                    <span className="t-name">{t.name}</span>
                    <span className="t-cat">{t.category}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
