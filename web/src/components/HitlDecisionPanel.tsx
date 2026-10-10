/**
 * HITL 决策面板组件
 * 用于查看上下文并做出决策
 */

import React, { useState, useCallback } from 'react';
import { hitlApi } from '../api/hitl';
import type { HitlQueueItem, HitlOption, HitlDecision } from '../types/hitl';
import './HitlDecisionPanel.css';

interface HitlDecisionPanelProps {
  item: HitlQueueItem;
  onClose: () => void;
}

export function HitlDecisionPanel({
  item,
  onClose,
}: HitlDecisionPanelProps): React.ReactElement {
  const [selectedOption, setSelectedOption] = useState<HitlOption | null>(null);
  const [customResolution, setCustomResolution] = useState<Record<string, unknown>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDecide = useCallback(
    async (decision: HitlDecision['decision']) => {
      if (!selectedOption && decision !== 'cancelled') {
        setError('请选择一个选项');
        return;
      }

      setSubmitting(true);
      setError(null);

      try {
        await hitlApi.decide(item.id, {
          decision: decision === 'approved' && selectedOption ? selectedOption.value : decision,
          resolution: Object.keys(customResolution).length > 0 ? customResolution : undefined,
        });
        onClose();
      } catch (err) {
        setError(err instanceof Error ? err.message : '提交失败');
      } finally {
        setSubmitting(false);
      }
    },
    [item.id, selectedOption, customResolution, onClose]
  );

  const createdAt = new Date(item.createdAt);
  const expiresAt = item.expiresAt ? new Date(item.expiresAt) : null;
  const remainingTime = expiresAt
    ? Math.max(0, Math.floor((expiresAt.getTime() - Date.now()) / 1000))
    : null;

  return (
    <div className="hitl-decision-panel__overlay" onClick={onClose}>
      <div
        className="hitl-decision-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="hitl-decision-title"
      >
        <div className="hitl-decision-panel__header">
          <h2 id="hitl-decision-title" className="hitl-decision-panel__title">
            人工决策
          </h2>
          <button
            className="hitl-decision-panel__close"
            onClick={onClose}
            aria-label="关闭"
          >
            ×
          </button>
        </div>

        <div className="hitl-decision-panel__meta">
          <div className="hitl-decision-panel__meta-item">
            <span className="hitl-decision-panel__meta-label">检查点</span>
            <span className="hitl-decision-panel__meta-value">{item.checkpoint}</span>
          </div>
          <div className="hitl-decision-panel__meta-item">
            <span className="hitl-decision-panel__meta-label">执行ID</span>
            <code className="hitl-decision-panel__meta-value">{item.executionId}</code>
          </div>
          <div className="hitl-decision-panel__meta-item">
            <span className="hitl-decision-panel__meta-label">创建时间</span>
            <span className="hitl-decision-panel__meta-value">
              {createdAt.toLocaleString('zh-CN')}
            </span>
          </div>
          {expiresAt && (
            <div className="hitl-decision-panel__meta-item">
              <span className="hitl-decision-panel__meta-label">剩余时间</span>
              <span
                className={`hitl-decision-panel__meta-value ${
                  remainingTime !== null && remainingTime < 300
                    ? 'hitl-decision-panel__meta-value--warning'
                    : ''
                }`}
              >
                {formatRemainingTime(remainingTime ?? 0)}
              </span>
            </div>
          )}
        </div>

        <div className="hitl-decision-panel__reason">
          <h3 className="hitl-decision-panel__section-title">决策原因</h3>
          <p className="hitl-decision-panel__reason-text">
            {item.reason || '无'}
          </p>
        </div>

        <div className="hitl-decision-panel__context">
          <h3 className="hitl-decision-panel__section-title">上下文详情</h3>
          <pre className="hitl-decision-panel__context-json">
            {JSON.stringify(item.context, null, 2)}
          </pre>
        </div>

        <div className="hitl-decision-panel__options">
          <h3 className="hitl-decision-panel__section-title">选择决策</h3>
          <div className="hitl-decision-panel__options-list">
            {item.options.map((option: HitlOption) => (
              <label
                key={option.value}
                className={`hitl-decision-panel__option ${
                  selectedOption?.value === option.value
                    ? 'hitl-decision-panel__option--selected'
                    : ''
                }`}
              >
                <input
                  type="radio"
                  name="decision"
                  value={option.value}
                  checked={selectedOption?.value === option.value}
                  onChange={() => setSelectedOption(option)}
                  className="hitl-decision-panel__option-input"
                />
                <span className="hitl-decision-panel__option-label">
                  {option.label}
                </span>
              </label>
            ))}
          </div>
        </div>

        {selectedOption && (
          <div className="hitl-decision-panel__resolution">
            <h3 className="hitl-decision-panel__section-title">
              补充信息（可选）
            </h3>
            <textarea
              className="hitl-decision-panel__resolution-input"
              placeholder="输入补充说明或修改参数..."
              value={JSON.stringify(customResolution)}
              onChange={(e) => {
                try {
                  setCustomResolution(JSON.parse(e.target.value || '{}'));
                } catch {
                  // 忽略无效 JSON
                }
              }}
            />
          </div>
        )}

        {error && (
          <div className="hitl-decision-panel__error">
            ⚠️ {error}
          </div>
        )}

        <div className="hitl-decision-panel__actions">
          <button
            className="hitl-decision-panel__btn hitl-decision-panel__btn--cancel"
            onClick={() => handleDecide('cancelled')}
            disabled={submitting}
          >
            取消
          </button>
          <button
            className="hitl-decision-panel__btn hitl-decision-panel__btn--reject"
            onClick={() => handleDecide('rejected')}
            disabled={submitting || !selectedOption}
          >
            拒绝
          </button>
          <button
            className="hitl-decision-panel__btn hitl-decision-panel__btn--approve"
            onClick={() => handleDecide('approved')}
            disabled={submitting || !selectedOption}
          >
            {submitting ? '提交中...' : '批准'}
          </button>
        </div>
      </div>
    </div>
  );
}

function formatRemainingTime(seconds: number): string {
  if (seconds <= 0) return '已过期';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;

  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分钟`;
  }
  if (minutes > 0) {
    return `${minutes} 分钟 ${secs} 秒`;
  }
  return `${secs} 秒`;
}
