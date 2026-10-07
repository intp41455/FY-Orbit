/**
 * A-离线优先-01/03 · 断网降级提示（前端侧）。
 *
 * 判据：断网时横幅**如实说明**——本地能力照常、哪些远程功能停了、怎么恢复；
 * 在线时**不**显示（默认离线是后端架构，不是「浏览器断网」，不能混为一谈、
 * 更不该常驻刷屏）。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';

import { OfflineBadge } from './ui';

/** 改写 navigator.onLine 并派发真实的 online/offline 事件。 */
function setOnline(online: boolean): void {
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    value: online,
  });
  act(() => {
    window.dispatchEvent(new Event(online ? 'online' : 'offline'));
  });
}

describe('OfflineBadge 断网降级提示', () => {
  beforeEach(() => {
    setOnline(true);
  });

  it('在线时不显示（不常驻刷屏）', () => {
    render(<OfflineBadge />);
    expect(screen.queryByTestId('offline-badge')).toBeNull();
  });

  it('断网时说明「本地照常 + 远程停用 + 怎么恢复」，而不是只说一句离线', () => {
    render(<OfflineBadge />);
    setOnline(false);

    const badge = screen.getByTestId('offline-badge');
    expect(badge).toHaveAttribute('role', 'alert');
    expect(badge).toHaveTextContent('本地能力照常');
    expect(badge).toHaveTextContent('云端模型相关功能');
    expect(badge).toHaveTextContent('已停用');
    expect(badge).toHaveTextContent('恢复联网后自动可用');
  });

  it('恢复联网后横幅自行消失', () => {
    render(<OfflineBadge />);
    setOnline(false);
    expect(screen.getByTestId('offline-badge')).toBeInTheDocument();

    setOnline(true);
    expect(screen.queryByTestId('offline-badge')).toBeNull();
  });
});
