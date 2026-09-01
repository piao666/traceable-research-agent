import type { ReactNode } from "react";
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

function NavItems({ mobile = false }: { mobile?: boolean }) {
  const items = mobile ? [...primaryNav, ["/system", "系统"] as const] : [...primaryNav, ...secondaryNav];
  return <>{items.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>{label}</NavLink>)}</>;
}

/** Figma AppShell — component 28:6. */
export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const routeName = location.pathname === "/" ? "概览" : location.pathname.startsWith("/research") ? "新建研究" : location.pathname.startsWith("/runs/") ? "计划审批" : location.pathname.startsWith("/runs") ? "研究任务" : "本地功能";
  return (
    <div className="app-shell" data-figma-node="28:6">
      <aside className="sidebar">
        <div className="brand">TRACEABLE RESEARCH</div>
        <div className="local-state">● 本地实例 · workspace 就绪</div>
        <nav className="side-nav" aria-label="主导航"><NavItems /></nav>
      </aside>
      <header className="topbar"><span>本地 workspace&nbsp; / &nbsp;{routeName}</span><span className="topbar-health">本地优先 · 单实例</span></header>
      <main className="app-content">{children}</main>
      <nav className="mobile-nav" aria-label="移动端主导航"><NavItems mobile /></nav>
    </div>
  );
}
