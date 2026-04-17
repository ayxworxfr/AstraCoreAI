import { Layout } from 'antd';
import ConversationSidebar from '../components/chat/ConversationSidebar';
import ChatMain from '../components/chat/ChatMain';

const { Sider, Content } = Layout;

export default function ChatPage(): JSX.Element {
  return (
    <Layout style={{ height: '100%', overflow: 'hidden' }}>
      <Sider
        width={300}
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
