import { Component, type ErrorInfo, type PropsWithChildren, type ReactNode } from "react";
import { Notice } from "./ui";

type State = {
  error: Error | null;
};

export class ErrorBoundary extends Component<PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Dashboard render error", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <Notice title="This page hit an error">
          {this.state.error.message || "Refresh the page and try again."}
        </Notice>
      );
    }
    return this.props.children;
  }
}
