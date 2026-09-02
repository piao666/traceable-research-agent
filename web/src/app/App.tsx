import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { Button } from "../components/primitives";
import { NewResearchPage } from "../pages/NewResearchPage";
import { OverviewPage } from "../pages/OverviewPage";
import { PlanReviewPage } from "../pages/PlanReviewPage";
import { RunsPage } from "../pages/RunsPage";
import { RunLayout } from "../components/RunLayout";
import { WorkbenchPage } from "../pages/WorkbenchPage";
import { EvidencePage } from "../pages/EvidencePage";
import { ReportPage } from "../pages/ReportPage";
import { SessionsPage, SessionPage } from "../pages/SessionsPage";
import { MemoryPage } from "../pages/MemoryPage";
import { CapabilitiesPage } from "../pages/CapabilitiesPage";
import { SystemPage } from "../pages/SystemPage";
import { PageBoundary } from "../components/PageBoundary";

function RoutedPages() {
  const location = useLocation();
  return <PageBoundary key={location.pathname}><PageRoutes /></PageBoundary>;
}

function NotFound() {
  return <div className="panel not-found"><h1>页面不存在</h1><p>请返回本地研究概览。</p><Button onClick={() => { window.location.href = "/"; }}>返回概览</Button></div>;
}

export function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppShell>
        <RoutedPages />
      </AppShell>
    </BrowserRouter>
  );
}

function PageRoutes() {
  return <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/research/new" element={<NewResearchPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId/plan" element={<PlanReviewPage />} />
          <Route path="/runs/:runId" element={<RunLayout />}>
            <Route index element={<WorkbenchPage />} />
            <Route path="evidence" element={<EvidencePage />} />
            <Route path="report" element={<ReportPage />} />
          </Route>
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:sessionId" element={<SessionPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/capabilities" element={<CapabilitiesPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/new" element={<Navigate to="/research/new" replace />} />
          <Route path="*" element={<NotFound />} />
        </Routes>;
}
