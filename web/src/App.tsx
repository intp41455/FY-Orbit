import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Layout } from './components/Layout';
import { ReviewMode } from './components/ReviewMode';
import { RequireAuth } from './components/RequireAuth';
import { LoginPage } from './pages/LoginPage';
import { ChatPage } from './pages/ChatPage';
import { HistoryPage } from './pages/HistoryPage';
import { GrowthPage } from './pages/GrowthPage';
import { AssessmentsPage } from './pages/AssessmentsPage';
import { WorkbenchPage } from './pages/WorkbenchPage';
import { ApprovalsPage } from './pages/ApprovalsPage';
import { SkillsPage } from './pages/SkillsPage';
import { PrivateSpacePage } from './pages/PrivateSpacePage';
import { CabinPage } from './pages/CabinPage';
import { SettingsPage } from './pages/SettingsPage';
import { CanvasPage } from './pages/CanvasPage';
import { ProfilesPage } from './pages/ProfilesPage';
import { DslCanvasPage } from './pages/DslCanvasPage';
import { ChatDebugPage } from './pages/ChatDebugPage';
import { AgentDispatchPage } from './pages/AgentDispatchPage';
import { KnowledgePage } from './pages/KnowledgePage';  // W3 本地知识库
import { HubPage } from './pages/HubPage';  // W6 超级中台适配器中心
import { AvatarWorkshopPage } from './pages/AvatarWorkshopPage';  // W11 角色工坊
import PetPage from './pet/PetPage';  // W10 桌面宠物浮窗

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <ReviewMode />
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/pet" element={<PetPage />} />  {/* W10 透明置顶宠物浮窗，不套 Layout/不要求登录 */}
            <Route
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/growth" element={<GrowthPage />} />
              <Route path="/assessments" element={<AssessmentsPage />} />
              <Route path="/workbench" element={<WorkbenchPage />} />
              <Route path="/canvas" element={<CanvasPage />} />
              <Route path="/dsl-canvas" element={<DslCanvasPage />} />
              <Route path="/chat-debug" element={<ChatDebugPage />} />
              <Route path="/agent-dispatch" element={<AgentDispatchPage />} />
              <Route path="/knowledge" element={<KnowledgePage />} />  {/* W3 本地知识库 */}
              <Route path="/profiles" element={<ProfilesPage />} />
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/private" element={<PrivateSpacePage />} />
              <Route path="/avatar" element={<AvatarWorkshopPage />} />  {/* W11 角色工坊 */}
              <Route path="/cabin" element={<CabinPage />} />
              <Route path="/hub" element={<HubPage />} />  {/* W6 超级中台适配器中心 */}
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
