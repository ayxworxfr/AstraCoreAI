import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import ChatPage from '../pages/ChatPage';
import RagPage from '../pages/RagPage';
import SystemPage from '../pages/SystemPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', element: <ChatPage /> },
      { path: 'rag', element: <RagPage /> },
      { path: 'system', element: <SystemPage /> },
    ],
  },
]);
