import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ConnectionForm } from './ConnectionForm';

describe('ConnectionForm 市场远程源（I-12）', () => {
  it('类型下拉含市场远程源，选中后提示填 url', () => {
    render(
      <ConnectionForm
        seed={null}
        editing={null}
        busy={false}
        notice=""
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const select = screen.getByTestId('hub-form-kind');
    expect(select.textContent).toContain('市场远程源');
    fireEvent.change(select, { target: { value: 'market_source' } });
    expect(screen.getByTestId('hub-market-source-hint').textContent).toContain('url');
  });
});
