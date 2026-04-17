import { Layout, Menu, Button, Flex } from 'antd';
import { BulbOutlined, MoonOutlined } from '@ant-design/icons';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useSettingsStore } from '../stores/settingsStore';

const { Header, Content } = Layout;

const NAV_ITEMS = [
  { key: '/chat', label: <NavLink to="/chat">对话</NavLink> },
  { key: '/rag', label: <NavLink to="/rag">RAG</NavLink> },
  { key: '/system', label: <NavLink to="/system">系统</NavLink> },
];

export default function AppShell(): JSX.Element {
  const { theme, toggleTheme } = useSettingsStore();
  const location = useLocation();

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          padding: '0 20px',
          flexShrink: 0,
          height: 56,
          lineHeight: '56px',
          borderBottom: theme === 'light' ? '1px solid #e8edf2' : '1px solid rgba(255,255,255,0.06)',
        }}
      >
        <Flex align="center" gap={8}>
          <Menu
            theme={theme === 'dark' ? 'dark' : 'light'}
            mode="horizontal"
            selectedKeys={[location.pathname]}
            items={NAV_ITEMS}
            style={{
              background: 'transparent',
              border: 'none',
              minWidth: 220,
              height: 56,
              lineHeight: '56px',
              fontSize: 14,
              fontWeight: 500,
            }}
          />
          <Button
            type="text"
            size="large"
            icon={theme === 'dark' ? <BulbOutlined /> : <MoonOutlined />}
            onClick={toggleTheme}
            style={{
              color: theme === 'light' ? 'rgba(0, 0, 0, 0.65)' : 'rgba(255,255,255,0.75)',
              width: 36,
              height: 36,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 8,
            }}
            title={theme === 'dark' ? '切换浅色' : '切换深色'}
          />
        </Flex>
      </Header>
      <Content style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <Outlet />
      </Content>
    </Layout>
  );
}
