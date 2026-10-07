import { Component, type ErrorInfo, type ReactNode } from 'react';

interface State {
  hasError: boolean;
  message: string;
}

interface Props {
  children: ReactNode;
  /** true = 自带 .main 外层（用于 Layout 之外的兜底）；默认 false，嵌在内容区里用。 */
  standalone?: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' };

  static getDerivedStateFromError(e: unknown): State {
    return { hasError: true, message: e instanceof Error ? e.message : String(e) };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    // Log a safe message only; never log private content.
    console.error('UI error boundary:', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      // 出错的路由路径显式写出来：既方便用户复述，也方便排查定位。
      const where =
        typeof window !== 'undefined' ? window.location.pathname + window.location.search : '';
      const body = (
        <div className="notice danger" data-testid="error-boundary">
          这个视图渲染出错了。你可以直接切到别的页面继续用，或点下面的按钮恢复。
          <div className="muted" style={{ marginTop: '0.4rem' }}>
            出错位置：{where || '（未知）'}
          </div>
          <div className="muted" style={{ marginTop: '0.2rem' }}>{this.state.message}</div>
          <div style={{ marginTop: '0.6rem', display: 'flex', gap: '0.5rem' }}>
            <button type="button" data-testid="error-retry" onClick={() => this.setState({ hasError: false, message: '' })}>
              重试
            </button>
            <button type="button" data-testid="error-reload" onClick={() => window.location.reload()}>
              重新加载
            </button>
            <button type="button" data-testid="error-home" onClick={() => window.location.assign('/chat')}>
              回到对话
            </button>
          </div>
        </div>
      );
      return this.props.standalone ? <div className="main">{body}</div> : body;
    }
    return this.props.children;
  }
}
