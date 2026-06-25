import { useState } from 'react';
import { Button, Drawer, Grid, Layout, theme } from 'antd';
import { MenuOutlined } from '@ant-design/icons';
import ConversationSidebar from '@/features/chat/components/ConversationSidebar';
import ChatMain from '@/features/chat/components/ChatMain';

const { Sider, Content } = Layout;
const { useBreakpoint } = Grid;

export default function ChatPage(): JSX.Element {
  const screens = useBreakpoint();
  const { token } = theme.useToken();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const isMobile = screens.md === false;

  return (
    <Layout style={{ height: '100%', overflow: 'hidden', position: 'relative' }}>
      {!isMobile && (
        <Sider
          width={260}
          theme="light"
          style={{
            overflow: 'hidden',
            height: '100%',
            borderRight: `1px solid ${token.colorBorderSecondary}`,
            background: token.colorBgContainer,
          }}
        >
          <ConversationSidebar />
        </Sider>
      )}
      <Content
        style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}
      >
        {isMobile && (
          <Button
            type="primary"
            shape="circle"
            icon={<MenuOutlined />}
            aria-label="打开会话列表"
            onClick={() => setDrawerOpen(true)}
            style={{
              position: 'absolute',
              top: 12,
              left: 12,
              zIndex: 20,
              boxShadow: token.boxShadowSecondary,
            }}
          />
        )}
        <ChatMain />
      </Content>
      {isMobile && (
        <Drawer
          title="会话"
          placement="left"
          width="min(86vw, 320px)"
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          styles={{
            body: { padding: 0 },
          }}
        >
          <ConversationSidebar onConversationSelected={() => setDrawerOpen(false)} />
        </Drawer>
      )}
    </Layout>
  );
}
