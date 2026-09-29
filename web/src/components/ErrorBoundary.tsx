import { Component, type ErrorInfo, type ReactNode } from 'react';

interface State {
  hasError: boolean;
  message: string;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
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
      return (
        <div className="main">
          <div className="notice danger">
            Something went wrong rendering this view. Refreshing usually helps.
            <div className="muted" style={{ marginTop: '0.4rem' }}>{this.state.message}</div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
