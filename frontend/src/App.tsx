import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { Empty, Loading } from "./components/ui";
import AuditPage from "./pages/AuditPage";
import ChatPage from "./pages/ChatPage";
import KeysPage from "./pages/KeysPage";
import LoginPage from "./pages/LoginPage";
import PermissionsPage from "./pages/PermissionsPage";
import ServersPage from "./pages/ServersPage";
import TeamPage from "./pages/TeamPage";
import ToolsPage from "./pages/ToolsPage";

function AdminOnly({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user?.role !== "admin") {
    return <Empty title="Admins only">You do not have access to this page.</Empty>;
  }
  return <>{children}</>;
}

export default function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="splash">
        <Loading label="Signing in" />
      </div>
    );
  }
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to={user.role === "admin" ? "/servers" : "/chat"} replace />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="tools" element={<ToolsPage />} />
        <Route path="keys" element={<KeysPage />} />
        <Route path="servers" element={<AdminOnly><ServersPage /></AdminOnly>} />
        <Route path="team" element={<AdminOnly><TeamPage /></AdminOnly>} />
        <Route path="permissions" element={<AdminOnly><PermissionsPage /></AdminOnly>} />
        <Route path="audit" element={<AdminOnly><AuditPage /></AdminOnly>} />
        <Route path="*" element={<Empty title="Page not found">That page does not exist.</Empty>} />
      </Route>
    </Routes>
  );
}
