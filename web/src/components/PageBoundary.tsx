import { Component, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button, PageHeader } from "./primitives";

export class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="page stack"><PageHeader title="页面暂时无法显示" subtitle="应用外壳和导航仍可使用；不会自动重新执行任务" /><p role="alert" className="error-banner">页面数据或渲染异常，请重新加载视图或返回任务列表。</p><Button variant="secondary" onClick={() => this.setState({ failed: false })}>重新加载视图</Button><Link to="/runs">返回任务列表</Link></div>;
  }
}
