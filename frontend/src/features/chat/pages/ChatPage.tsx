import { Layout } from 'antd';
import ConversationSidebar from '@/features/chat/components/ConversationSidebar';
import ChatMain from '@/features/chat/components/ChatMain';

const { Sider, Content } = Layout;

export default function ChatPage(): JSX.Element {
  return (
    <Layout style={{ height: '100%', overflow: 'hidden' }}>
      <Sider
        width={260}
        theme="light"
        style={{
          overflow: 'hidden',
          height: '100%',
          borderRight: '1px solid rgba(5, 5, 5, 0.06)',
        }}
      >
        <ConversationSidebar />
      </Sider>
      <Content
        style={{ flex: '1 1 0', minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}
      >
        <ChatMain />
      </Content>
    </Layout>
  );
}
