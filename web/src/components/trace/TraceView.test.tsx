import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TraceView } from './TraceView';
import { validatePmiTraceEvent, validateTraceDocument } from './validateTrace';
import pmiSamples from './samples/pmi-sample-traces.json';
import canvasSample from './samples/canvas-hermes-roundtrip.json';

/** 复制一份样例事件做负例变异，避免污染导入的模块数据。 */
function cloneEvent(layer: 'L1' | 'L2' | 'L3'): Record<string, unknown> {
  const doc = pmiSamples as { trace_events: Record<string, unknown>[] };
  const ev = doc.trace_events.find((e) => e.layer === layer);
  if (!ev) throw new Error(`sample ${layer} missing`);
  return JSON.parse(JSON.stringify(ev)) as Record<string, unknown>;
}

describe('validateTrace — PMI Trace Schema v1.0', () => {
  it('三层定稿样例全部通过校验（0 失败）', () => {
    const result = validateTraceDocument(pmiSamples);
    expect(result.format).toBe('pmi-trace');
    expect(result.eventCount).toBe(3);
    expect(result.failCount).toBe(0);
    expect(result.ok).toBe(true);
  });

  it('单事件校验器对 L1/L2/L3 各自通过', () => {
    for (const layer of ['L1', 'L2', 'L3'] as const) {
      const r = validatePmiTraceEvent(cloneEvent(layer));
      expect(r.failCount).toBe(0);
    }
  });

  it('负例：明文外传（plaintext_egress=true）被拦截', () => {
    const ev = cloneEvent('L2');
    const egress = ev.egress as Record<string, unknown>;
    egress.plaintext_egress = true;
    const r = validatePmiTraceEvent(ev);
    expect(r.ok).toBe(false);
    expect(r.issues.some((i) => i.status === 'fail' && i.field.includes('plaintext_egress'))).toBe(true);
  });

  it('负例：L1 规则层出现写回被拦截', () => {
    const ev = cloneEvent('L1');
    ev.writeback = { action: 'memory.upsert', target_ids: ['mem-x'], audit_seq: 1, audit_hash: 'a'.repeat(64), revision: 1 };
    const r = validatePmiTraceEvent(ev);
    expect(r.ok).toBe(false);
    expect(r.issues.some((i) => i.status === 'fail' && i.path.includes('allOf L1'))).toBe(true);
  });

  it('负例：缺失必备字段（hits/trace_id）被标记为缺失项', () => {
    const ev = cloneEvent('L1');
    delete ev.hits;
    delete ev.trace_id;
    const r = validatePmiTraceEvent(ev);
    expect(r.ok).toBe(false);
    expect(r.issues.some((i) => i.status === 'fail' && i.field === 'hits' && i.actual === '<missing>')).toBe(true);
    expect(r.issues.some((i) => i.status === 'fail' && i.field === 'trace_id' && i.actual === '<missing>')).toBe(true);
  });

  it('负例：未声明字段（additionalProperties）被拒绝', () => {
    const ev = cloneEvent('L1');
    ev.sneaky_field = 'bypass';
    const r = validatePmiTraceEvent(ev);
    expect(r.ok).toBe(false);
    expect(r.issues.some((i) => i.status === 'fail' && i.field.includes('sneaky_field'))).toBe(true);
  });

  it('负例：断网命中冷层被拦截', () => {
    const ev = cloneEvent('L2');
    const qm = ev.query_method as Record<string, unknown>;
    qm.index_tier = 'cold_remote';
    qm.online = false;
    const r = validatePmiTraceEvent(ev);
    expect(r.ok).toBe(false);
    expect(r.issues.some((i) => i.status === 'fail' && i.path.includes('online=false'))).toBe(true);
  });

  it('canvas 真实往返 trace 走结构校验且通过', () => {
    const r = validateTraceDocument(canvasSample);
    expect(r.format).toBe('canvas-timeline');
    expect(r.ok).toBe(true);
    expect(r.eventCount).toBe(10);
  });
});

describe('TraceView 渲染', () => {
  it('加载 L2 样例：校验通过、渲染节点、点击打开详情浮层', () => {
    render(<TraceView />);
    fireEvent.click(screen.getByTestId('sample-l2'));
    expect(screen.getByTestId('trace-validation-summary')).toHaveTextContent('schema 校验通过');
    expect(screen.getByTestId('trace-node-1')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('trace-node-1'));
    const detail = screen.getByTestId('trace-detail');
    expect(detail).toHaveTextContent('memory.upsert');
    expect(detail).toHaveTextContent('trace_id');
    fireEvent.click(screen.getByTestId('trace-detail-close'));
    expect(screen.queryByTestId('trace-detail')).not.toBeInTheDocument();
  });

  it('完整三层样例集渲染 3 个节点且泳道包含 actor/agent/存储层', () => {
    render(<TraceView />);
    fireEvent.click(screen.getByTestId('sample-full'));
    expect(screen.getByTestId('trace-node-1')).toBeInTheDocument();
    expect(screen.getByTestId('trace-node-2')).toBeInTheDocument();
    expect(screen.getByTestId('trace-node-3')).toBeInTheDocument();
    const lanes = screen.getAllByTestId('trace-lane').map((el) => el.textContent);
    expect(lanes).toContain('owner-local');
    expect(lanes).toContain('Hermes');
    expect(lanes).toContain('L1 规则层');
    expect(lanes).toContain('L2 记忆层');
    expect(lanes).toContain('L3 资料层');
  });

  it('canvas 真实 trace 渲染 10 个节点，泳道含 canvas 与 Hermes', () => {
    render(<TraceView />);
    fireEvent.click(screen.getByTestId('sample-canvas'));
    expect(screen.getByTestId('trace-validation-summary')).toHaveTextContent('格式: canvas-timeline');
    for (let i = 1; i <= 10; i++) expect(screen.getByTestId(`trace-node-${i}`)).toBeInTheDocument();
    const lanes = screen.getAllByTestId('trace-lane').map((el) => el.textContent);
    expect(lanes).toContain('canvas');
    expect(lanes).toContain('Hermes');
  });
});
