import { useEffect, useRef, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

const primaryNav = [
  ["/", "概览"],
  ["/research/new", "新建研究"],
  ["/runs", "研究任务"],
] as const;

const secondaryNav = [
  ["/sessions", "会话"],
  ["/memory", "记忆"],
  ["/capabilities", "能力"],
  ["/system", "系统与质量"],
] as const;

function NavItems() {
  const items = [...primaryNav, ...secondaryNav];
  return <>{items.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>{label}</NavLink>)}</>;
}

/** Figma AppShell — component 28:6. */
export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const previousPath = useRef(location.pathname);
  const routeName = location.pathname === "/" ? "概览" : location.pathname.startsWith("/research") ? "新建研究" : location.pathname.startsWith("/runs/") ? (location.pathname.endsWith("/plan") ? "计划审批" : location.pathname.endsWith("/evidence") ? "证据追踪" : location.pathname.endsWith("/report") ? "研究报告" : "实时工作台") : location.pathname.startsWith("/runs") ? "研究任务" : secondaryNav.find(([path]) => location.pathname.startsWith(path))?.[1] || "本地功能";
  useEffect(() => {
    document.title = `${routeName} · Traceable Research Agent`;
    if (previousPath.current !== location.pathname) {
      previousPath.current = location.pathname;
      document.getElementById("main-content")?.focus({ preventScroll: true });
      window.scrollTo?.({ top: 0, left: 0, behavior: "instant" });
    }
  }, [location.pathname, routeName]);
  return (
    <div className="app-shell" data-figma-node="28:6">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="sidebar">
        <div className="brand">TRACEABLE RESEARCH</div>
        <div className="local-state">本地实例 · 数据保存在部署端</div>
        <nav className="side-nav" aria-label="主导航"><NavItems /></nav>
      </aside>
      <header className="topbar"><span>本地 workspace&nbsp; / &nbsp;{routeName}</span><span className="topbar-health">本地优先 · 单实例</span></header>
      <main className="app-content" id="main-content" tabIndex={-1} aria-label={routeName}>{children}</main>
      <nav className="mobile-nav" aria-label="移动端主导航"><NavItems /></nav>
    </div>
  );
}
