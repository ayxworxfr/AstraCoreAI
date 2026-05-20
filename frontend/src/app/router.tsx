import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import ChatPage from '../pages/ChatPage';
import MemoryPage from '../pages/MemoryPage';
import RagPage from '../pages/RagPage';
import SkillsPage from '../pages/SkillsPage';
import SystemPage from '../pages/SystemPage';

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
