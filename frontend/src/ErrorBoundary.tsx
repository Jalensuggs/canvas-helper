import React, { Component, ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Rendered instead of the default card, when a caller wants its own copy. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Without this, any render-time exception unmounts the whole tree and the user
 * is left looking at a blank page with no way back.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("界面渲染失败", error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="state-card">
        <h3>这个页面出错了</h3>
        <p>{error.message || "发生了未知错误。"}</p>
        <button className="button soft" onClick={this.reset}>
          重试
        </button>
      </div>
    );
  }
}
