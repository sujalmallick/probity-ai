import { Component, type ReactNode } from "react";
import { AlertTriangle, RotateCw } from "lucide-react";

/** Catches a crash while rendering a page so the rest of the app (sidebar, navigation) keeps working.
 *  Give it a `resetKey` (e.g. the path) so moving to another page clears the error. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("Page crashed", error);
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="card mx-auto mt-10 flex max-w-md flex-col items-center gap-3 p-6 text-center">
        <AlertTriangle size={22} className="text-medium" aria-hidden />
        <div className="font-semibold">This page hit a problem</div>
        <p className="text-sm text-muted">Nothing was changed. Reload the page to try again; your data is safe on the server.</p>
        <div className="flex gap-2">
          <button className="btn btn-primary" onClick={() => window.location.reload()}><RotateCw size={14} />Reload</button>
          <a className="btn" href="/dashboard">Go to dashboard</a>
        </div>
      </div>
    );
  }
}
