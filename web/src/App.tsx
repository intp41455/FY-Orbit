import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Layout } from './components/Layout';
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
import { SettingsPage } from './pages/SettingsPage';
import { CanvasPage } from './pages/CanvasPage';
import { ProfilesPage } from './pages/ProfilesPage';

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
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
              <Route path="/profiles" element={<ProfilesPage />} />
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/private" element={<PrivateSpacePage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
