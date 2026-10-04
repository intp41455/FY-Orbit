"""W9 音频通道 v1：纯 Python 芯片音乐合成器（零外部依赖、零模型、零网络）。

为什么先做「代码合成」（任务书 §1.3）：未配置任何 key 时，个人空间的音乐模块也必须有
**真实产出**——不能是占位。本模块用 ``wave`` + ``math`` 直接合成 44100Hz / 单声道 /
16bit 的 WAV，音色是方波/三角波/噪声的芯片乐，参数由「情绪」映射到音阶、和弦进行、
节奏型与音色，因此「选情绪 → 拿到曲子」是真实计算，不是随机噪声也不是假数据。

安全约束（任务书 §3）：

* 长度 ≤ ``MAX_SECONDS``（60s），超出直接截断并在返回元数据里如实标注；
* 峰值归一化到 ``PEAK_CEILING``（0.89）并做软限幅，避免爆音；
* 采样值转 int16 前做 ``min/max`` 夹紧，杜绝溢出回绕。

**诚实边界**：这里生成的是**程序化芯片音乐**，不是「AI 作曲」。返回的 meta 里
``engine="chiptune-synth-1.0"``、``provider="local_synth"``，前端必须照实展示，
不得标成「模型生成」。TTS 语音走 OpenAI 兼容 ``audio/speech``（见 ``imagegen`` 里的
provider 配置），未配置时诚实报错。
"""

from __future__ import annotations

import io
import math
import struct
import wave
from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidationFailed

SAMPLE_RATE = 44100
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16bit
MAX_SECONDS = 60
MIN_SECONDS = 2
PEAK_CEILING = 0.89

#: 情绪 → 音乐参数。键是前端可选值（必须与 web/src/api/assets.ts 的 MOODS 对齐）。
MOODS: dict[str, dict[str, Any]] = {
    "calm": {
        "label": "平静",
        # 小调五声音阶（A 小调 pentatonic），慢速，长音
        "scale": [220.00, 261.63, 293.66, 329.63, 392.00, 440.00],
        "tempo_bpm": 72,
        "waveform": "triangle",
        "chords": [[0, 2, 4], [3, 5, 0]],
        "pattern": [1, 0, 1, 0, 1, 0, 0, 1],
        "note_seconds": 0.55,
        "attack": 0.02,
        "release": 0.30,
        "detune": 0.0,
    },
    "bright": {
        "label": "明亮",
        "scale": [261.63, 293.66, 329.63, 392.00, 440.00, 523.25],
        "tempo_bpm": 108,
        "waveform": "square",
        "chords": [[0, 2, 4], [4, 5, 2], [2, 4, 5]],
        "pattern": [1, 1, 0, 1, 1, 0, 1, 0],
        "note_seconds": 0.26,
        "attack": 0.008,
        "release": 0.12,
        "detune": 0.004,
    },
    "dreamy": {
        "label": "梦境",
        "scale": [196.00, 233.08, 261.63, 293.66, 349.23, 392.00],
        "tempo_bpm": 84,
        "waveform": "sine",
        "chords": [[0, 2, 4], [5, 3, 1]],
        "pattern": [1, 0, 1, 0, 0, 1, 1, 0],
        "note_seconds": 0.44,
        "attack": 0.06,
        "release": 0.42,
        "detune": 0.006,
    },
    "energetic": {
        "label": "活力",
        "scale": [261.63, 329.63, 392.00, 440.00, 523.25, 659.25],
        "tempo_bpm": 138,
        "waveform": "square",
        "chords": [[0, 2, 4], [3, 4, 5]],
        "pattern": [1, 0, 1, 1, 1, 0, 1, 0],
        "note_seconds": 0.18,
        "attack": 0.005,
        "release": 0.09,
        "detune": 0.0,
    },
    "moody": {
        "label": "沉思",
        "scale": [174.61, 207.65, 233.08, 261.63, 311.13, 349.23],
        "tempo_bpm": 66,
        "waveform": "triangle",
        "chords": [[0, 2, 4], [3, 0, 2]],
        "pattern": [1, 0, 0, 1, 0, 1, 0, 0],
        "note_seconds": 0.62,
        "attack": 0.03,
        "release": 0.36,
        "detune": 0.003,
    },
}

MOOD_IDS: tuple[str, ...] = tuple(MOODS)


@dataclass(frozen=True)
class SynthResult:
    """一次合成的真实产物。``truncated`` 如实标注被裁掉的部分。"""

    wav: bytes
    meta: dict[str, Any] = field(default_factory=dict)


def _oscillator(kind: str, phase: float) -> float:
    """归一化到 [-1, 1] 的基础波形。"""
    frac = phase % 1.0
    if kind == "square":
        return 1.0 if frac < 0.5 else -1.0
    if kind == "triangle":
        return 4.0 * abs(frac - 0.5) - 1.0
    if kind == "saw":
        return 2.0 * frac - 1.0
    # sine
    return math.sin(2.0 * math.pi * frac)


def _soft_clip(value: float) -> float:
    """软限幅：|x| 越大增益越小，天然防爆音且不失真得刺耳。"""
    return math.tanh(value * 1.2)


def _envelope(index: int, total: int, attack: int, release: int) -> float:
    if attack > 0 and index < attack:
        return index / attack
    if release > 0 and index > total - release:
        return max(0.0, (total - index) / release)
    return 1.0


def render_wav(
    *,
    mood: str,
    seconds: float,
    seed: int = 0,
) -> SynthResult:
    """按情绪合成 WAV。确定性：同 (mood, seconds, seed) 必得逐字节相同结果。"""
    if mood not in MOODS:
        raise ValidationFailed(
            "asset_mood_invalid", f"mood must be one of {list(MOOD_IDS)}"
        )
    try:
        dur = float(seconds)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("asset_seconds_invalid", "seconds must be a number") from exc
    truncated = dur > MAX_SECONDS
    dur = max(MIN_SECONDS, min(dur, MAX_SECONDS))

    spec = MOODS[mood]
    scale: list[float] = list(spec["scale"])
    step_seconds = 60.0 / float(spec["tempo_bpm"]) * float(spec["note_seconds"]) * 2.0
    total_frames = int(SAMPLE_RATE * dur)
    samples: list[float] = [0.0] * total_frames

    cursor = 0.0
    step_index = 0
    while cursor < dur:
        chord = spec["chords"][step_index % len(spec["chords"])]
        pattern = spec["pattern"]
        if pattern[step_index % len(pattern)]:
            for degree in chord:
                freq = scale[degree % len(scale)]
                # detune 让同音高的两个声部微微错开，产生芯片乐的合唱感
                if spec["detune"]:
                    freq *= 1.0 + spec["detune"] * ((degree % 2) * 2 - 1)
                start = int(cursor * SAMPLE_RATE)
                length = int(step_seconds * SAMPLE_RATE)
                attack = max(1, int(spec["attack"] * SAMPLE_RATE))
                release = max(1, int(spec["release"] * SAMPLE_RATE))
                for i in range(length):
                    idx = start + i
                    if idx >= total_frames:
                        break
                    phase = freq * (i / SAMPLE_RATE)
                    env = _envelope(i, length, attack, release)
                    samples[idx] += _oscillator(spec["waveform"], phase) * env / len(chord)
        cursor += step_seconds
        step_index += 1

    # 噪声鼓点：确定性伪随机（seed 只作偏移），给节奏一点颗粒感。
    if mood in ("energetic", "bright"):
        rng = _Lcg(seed or 1)
        beat = max(1, int(0.5 * SAMPLE_RATE))
        for start in range(0, total_frames, beat):
            for i in range(min(1200, total_frames - start)):
                samples[start + i] += (rng.next() * 2.0 - 1.0) * 0.12 * (1.0 - i / 1200.0)

    peak = max((abs(s) for s in samples), default=0.0)
    gain = (PEAK_CEILING / peak) if peak > 0 else 1.0

    pcm = bytearray()
    for value in samples:
        scaled = _soft_clip(value * gain) * PEAK_CEILING
        # 夹紧后再转 int16，杜绝溢出回绕（wrap-around 会产生爆音）
        clamped = max(-1.0, min(1.0, scaled))
        pcm += struct.pack("<h", int(clamped * 32767))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(pcm))

    meta = {
        "engine": "chiptune-synth-1.0",
        "provider": "local_synth",
        "mood": mood,
        "mood_label": spec["label"],
        "tempo_bpm": spec["tempo_bpm"],
        "waveform": spec["waveform"],
        "notes": step_index,
        "requested_seconds": round(float(seconds), 3),
        "seconds": round(dur, 3),
        "truncated": truncated,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "peak": round(PEAK_CEILING, 3),
        "mime": "audio/wav",
    }
    return SynthResult(wav=buffer.getvalue(), meta=meta)


class _Lcg:
    """确定性线性同余发生器（合成器内部用，**不**用于安全用途）。"""

    def __init__(self, seed: int) -> None:
        self._state = (seed * 2654435761 + 1013904223) & 0xFFFFFFFF

    def next(self) -> float:
        self._state = (self._state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self._state / 0xFFFFFFFF


def mood_catalog() -> list[dict[str, Any]]:
    """前端情绪选择器数据源（标签 + 参数摘要，绝不含内部实现细节）。"""
    return [
        {
            "id": mood,
            "label": MOODS[mood]["label"],
            "tempo_bpm": MOODS[mood]["tempo_bpm"],
            "waveform": MOODS[mood]["waveform"],
        }
        for mood in MOOD_IDS
    ]