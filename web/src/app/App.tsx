import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { Button, Panel } from "../components/primitives";
import { NewResearchPage } from "../pages/NewResearchPage";
import { OverviewPage } from "../pages/OverviewPage";
import { PlanReviewPage } from "../pages/PlanReviewPage";
import { RunsPage } from "../pages/RunsPage";

function PlannedRoute({ title }: { title: string }) {
  return <div className="page"><Panel title={title}>该路由将在 D05–D11 实现阶段接入；当前 Phase 8 首批交付覆盖 D01–D04。</Panel></div>;
}

function NotFound() {
  return <div className="panel not-found"><h1>页面不存在</h1><p>请返回本地研究概览。</p><Button onClick={() => { window.location.href = "/"; }}>返回概览</Button></div>;
}

export function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppShell>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/research/new" element={<NewResearchPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId/plan" element={<PlanReviewPage />} />
          <Route path="/runs/:runId" element={<PlannedRoute title="实时工作台" />} />
          <Route path="/sessions" element={<PlannedRoute title="会话" />} />
          <Route path="/memory" element={<PlannedRoute title="记忆" />} />
          <Route path="/capabilities" element={<PlannedRoute title="能力" />} />
          <Route path="/system" element={<PlannedRoute title="系统与质量" />} />
          <Route path="/new" element={<Navigate to="/research/new" replace />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
