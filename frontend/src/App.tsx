import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { ApprovalProvider } from "./context/ApprovalContext";
import { DemoPersonaProvider } from "./context/DemoPersonaContext";
import { AccessRequestsPage } from "./pages/AccessRequestsPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { AssistantPage } from "./pages/AssistantPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SystemPage } from "./pages/SystemPage";

export function App() {
  return (
    <DemoPersonaProvider>
      <ApprovalProvider>
        <AppShell>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/overview" element={<OverviewPage />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/access-requests" element={<AccessRequestsPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
      </ApprovalProvider>
    </DemoPersonaProvider>
  );
}
