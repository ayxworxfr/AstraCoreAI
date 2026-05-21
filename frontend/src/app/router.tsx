import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import ChatPage from '@/features/chat/pages/ChatPage';
import MemoryPage from '@/features/memory/pages/MemoryPage';
import RagPage from '@/features/rag/pages/RagPage';
import SkillsPage from '@/features/skills/pages/SkillsPage';
import SystemPage from '@/features/system/pages/SystemPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', element: <ChatPage /> },
      { path: 'memory', element: <MemoryPage /> },
      { path: 'rag', element: <RagPage /> },
      { path: 'skills', element: <SkillsPage /> },
      { path: 'system', element: <SystemPage /> },
    ],
  },
]);
