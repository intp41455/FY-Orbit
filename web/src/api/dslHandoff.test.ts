/**
 * G4-k 开箱模板→工坊通道：sessionStorage 一次性交接契约。
 *
 * write → take 往返；take 消费一次（读后即删，刷新不重复载入）；
 * 空/坏数据返回 null 且不抛错（绝不因交接损坏把工坊页炸掉）。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { takeDslHandoff, writeDslHandoff } from './dslHandoff';
import type { DslDocument } from './dslCanvas';

const DOC: DslDocument = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: 'x' } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [{ from: 'in1', to: 'out1' }],
};

beforeEach(() => {
  sessionStorage.clear();
});

describe('dslHandoff', () => {
  it('write→take 往返且只消费一次', () => {
    writeDslHandoff(DOC, '开箱模板:t1');
    const got = takeDslHandoff();
    expect(got?.doc).toEqual(DOC);
    expect(got?.source).toBe('开箱模板:t1');
    expect(takeDslHandoff()).toBeNull();
  });

  it('空交接返回 null', () => {
    expect(takeDslHandoff()).toBeNull();
  });

  it('坏数据返回 null 且不抛错', () => {
    sessionStorage.setItem('fy-orbit.dsl-handoff', '{broken json');
    expect(takeDslHandoff()).toBeNull();
    sessionStorage.setItem('fy-orbit.dsl-handoff', JSON.stringify({ nope: 1 }));
    expect(takeDslHandoff()).toBeNull();
  });
});
