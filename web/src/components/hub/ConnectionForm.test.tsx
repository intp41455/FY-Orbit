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

describe('ConnectionForm 外部 A2A Agent（G7）', () => {
  function renderForm() {
    return render(
      <ConnectionForm
        seed={null}
        editing={null}
        busy={false}
        notice=""
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
  }

  it('类型下拉含外部 A2A Agent', () => {
    renderForm();
    expect(screen.getByTestId('hub-form-kind').textContent).toContain('外部 A2A Agent');
  });

  it('选中后提示填 base_url 并说明探活是真调用', () => {
    renderForm();
    fireEvent.change(screen.getByTestId('hub-form-kind'), { target: { value: 'a2a_agent' } });
    const hint = screen.getByTestId('hub-a2a-agent-hint');
    expect(hint.textContent).toContain('base_url');
    expect(hint.textContent).toContain('/.well-known/agent.json');
    expect(hint.textContent).toContain('message/send');
    // 其他 kind 的提示不应串场
    expect(screen.queryByTestId('hub-market-source-hint')).toBeNull();
  });

  it('base_url 走明文 config、token 走 credentials', () => {
    const onSubmit = vi.fn();
    render(
      <ConnectionForm
        seed={null}
        editing={null}
        busy={false}
        notice=""
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByTestId('hub-form-kind'), { target: { value: 'a2a_agent' } });
    fireEvent.change(screen.getByTestId('hub-form-name'), { target: { value: '外部规划 Agent' } });

    fireEvent.click(screen.getByText('添加参数'));
    const keyInput = document.querySelector('input[placeholder="键，如 base_url"]') as HTMLInputElement;
    const valInput = document.querySelector('input[placeholder="值"]') as HTMLInputElement;
    fireEvent.change(keyInput, { target: { value: 'base_url' } });
    fireEvent.change(valInput, { target: { value: 'https://agent.example.com' } });

    const secret = document.querySelector('input[placeholder="填写新值"]') as HTMLInputElement;
    fireEvent.change(secret, { target: { value: 'tok-1' } });
    fireEvent.click(screen.getByTestId('hub-form-submit'));

    const payload = onSubmit.mock.calls[0]?.[0];
    expect(payload?.kind).toBe('a2a_agent');
    expect(payload?.config.base_url).toBe('https://agent.example.com');
    expect(payload?.credentials.api_key).toBe('tok-1');
    expect(payload?.secret_fields).toContain('api_key');
  });
});
