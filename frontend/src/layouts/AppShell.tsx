import { useEffect, useRef, useState } from 'react';
import { Avatar, Badge, Button, Dropdown, Layout, Menu, Space, Typography } from 'antd';
import { LogoutOutlined, MoonOutlined, RocketOutlined, SunOutlined } from '@ant-design/icons';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { useAuthStore } from '@/features/auth/store/authStore';
import { useSkillStore } from '@/features/skills/store/skillStore';
import { useSystemStore } from '@/features/system/store/systemStore';
import { apiClient } from '@/shared/services/apiClient';

const { Header, Content } = Layout;

const SCHEDULING_ENABLED = import.meta.env.VITE_FEATURE_SCHEDULING !== 'false';
const HEADER_BRAND_WIDTH = 180;
const HEADER_USER_WIDTH = 132;

export default function AppShell(): JSX.Element {
  const { theme, toggleTheme } = useSettingsStore();
  const fetchSettings = useSkillStore((s) => s.fetchSettings);
  const { systemInfo, fetchSystemInfo } = useSystemStore();
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuthStore();

  const ragEnabled = systemInfo?.rag_enabled ?? true;

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [settingsReady, setSettingsReady] = useState(false);
  const keepOpenRef = useRef(false);

  const [pendingApprovalsCount, setPendingApprovalsCount] = useState(0);

  useEffect(() => {
    void fetchSettings().finally(() => setSettingsReady(true));
    void fetchSystemInfo();
  }, [fetchSettings, fetchSystemInfo]);

  useEffect(() => {
    const fetchCount = () => {
      void apiClient
        .get<{ total: number }>('/api/v1/memory/pending-approvals', { params: { limit: 1 } })
        .then(({ data }) => setPendingApprovalsCount(data.total))
        .catch(() => undefined);
    };
    fetchCount();
    const timer = setInterval(fetchCount, 60_000);
    return () => clearInterval(timer);
  }, []);

  const navItems = [
    { key: '/chat', label: <NavLink to="/chat">对话</NavLink> },
    { key: '/skills', label: <NavLink to="/skills">Skill</NavLink> },
    {
      key: '/memory',
      label: (
        <NavLink to="/memory">
          <Badge count={pendingApprovalsCount} size="small" offset={[6, -2]}>
            Memory
          </Badge>
        </NavLink>
      ),
    },
    ...(ragEnabled ? [{ key: '/rag', label: <NavLink to="/rag">RAG</NavLink> }] : []),
    ...(SCHEDULING_ENABLED ? [{ key: '/scheduled-tasks', label: <NavLink to="/scheduled-tasks">任务</NavLink> }] : []),
    { key: '/system', label: <NavLink to="/system">系统</NavLink> },
  ];

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '0 16px',
          flexShrink: 0,
          height: 64,
          lineHeight: '64px',
          borderBottom: theme === 'light' ? '1px solid #e8edf2' : '1px solid rgba(255,255,255,0.06)',
        }}
      >
        <div style={{ width: HEADER_BRAND_WIDTH, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <RocketOutlined style={{ fontSize: 20, color: '#1677ff' }} />
          <Typography.Text strong style={{ fontSize: 15, letterSpacing: '-0.01em' }}>
            AstraCoreAI
          </Typography.Text>
        </div>
        <div style={{ flex: 1, display: 'flex', justifyContent: 'center', minWidth: 0 }}>
          <Menu
            theme={theme === 'dark' ? 'dark' : 'light'}
            mode="horizontal"
            selectedKeys={[location.pathname]}
            items={navItems}
            style={{
              background: 'transparent',
              border: 'none',
              height: 64,
              lineHeight: '64px',
              fontSize: 14,
              fontWeight: 500,
              minWidth: 460,
            }}
          />
        </div>
        <div style={{ width: HEADER_USER_WIDTH, flexShrink: 0, display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
          {user && (
            <Dropdown
              trigger={['click']}
              placement="bottomRight"
              open={dropdownOpen}
              onOpenChange={(next, info) => {
                if (!next && info.source === 'menu' && keepOpenRef.current) {
                  keepOpenRef.current = false;
                  return;
                }
                keepOpenRef.current = false;
                setDropdownOpen(next);
              }}
              menu={{
                items: [
                  {
                    key: 'info',
                    label: (
                      <div style={{ padding: '2px 0', pointerEvents: 'none', userSelect: 'none' }}>
                        <Typography.Text strong style={{ fontSize: 13, display: 'block' }}>{user.username}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {user.role === 'admin' ? '管理员' : '普通用户'}
                        </Typography.Text>
                      </div>
                    ),
                    disabled: true,
                  },
                  { type: 'divider' as const },
                  {
                    key: 'theme',
                    icon: <SunOutlined />,
                    label: (
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minWidth: 148 }}>
                        <span>主题</span>
                        <Space.Compact size="small" onClick={(e) => e.stopPropagation()}>
                          <Button
                            size="small"
                            type={theme === 'light' ? 'primary' : 'default'}
                            icon={<SunOutlined />}
                            onClick={(e) => { e.stopPropagation(); if (theme !== 'light') toggleTheme(); }}
                          />
                          <Button
                            size="small"
                            type={theme === 'dark' ? 'primary' : 'default'}
                            icon={<MoonOutlined />}
                            onClick={(e) => { e.stopPropagation(); if (theme !== 'dark') toggleTheme(); }}
                          />
                        </Space.Compact>
                      </div>
                    ),
                    onClick: () => { keepOpenRef.current = true; },
                  },
                  { type: 'divider' as const },
                  {
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    label: '退出登录',
                    danger: true,
                    onClick: handleLogout,
                  },
                ],
              }}
            >
              <Avatar
                size={32}
                style={{
                  backgroundColor: user.role === 'admin' ? '#faad14' : '#1677ff',
                  fontSize: 13,
                  fontWeight: 700,
                  cursor: 'pointer',
                  userSelect: 'none',
                  flexShrink: 0,
                }}
              >
                {user.username[0].toUpperCase()}
              </Avatar>
            </Dropdown>
          )}
        </div>
      </Header>
      <Content style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {settingsReady ? <Outlet /> : null}
      </Content>
    </Layout>
  );
}
