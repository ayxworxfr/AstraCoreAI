import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import ChatPage from '@/features/chat/pages/ChatPage';
import MemoryPage from '@/features/memory/pages/MemoryPage';
import PendingApprovalsPage from '@/features/memory/pages/PendingApprovalsPage';
import RagPage from '@/features/rag/pages/RagPage';
import SkillsPage from '@/features/skills/pages/SkillsPage';
import SystemPage from '@/features/system/pages/SystemPage';
import LoginPage from '@/features/auth/pages/LoginPage';
import { useAuthStore } from '@/features/auth/store/authStore';

const RAG_ENABLED = import.meta.env.VITE_FEATURE_RAG !== 'false';

function ProtectedRoute(): JSX.Element {
  const token = useAuthStore((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/chat" replace /> },
          { path: 'chat', element: <ChatPage /> },
          { path: 'memory', element: <MemoryPage /> },
          { path: 'memory/approvals', element: <PendingApprovalsPage /> },
          ...(RAG_ENABLED ? [{ path: 'rag', element: <RagPage /> }] : []),
          { path: 'skills', element: <SkillsPage /> },
          { path: 'system', element: <SystemPage /> },
        ],
      },
    ],
  },
]);
