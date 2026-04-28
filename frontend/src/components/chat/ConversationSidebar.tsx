import { useEffect, useRef, useState } from 'react';
import { Button, Input, Flex, Modal, Typography } from 'antd';
import { EditOutlined, RocketOutlined } from '@ant-design/icons';
import { Conversations } from '@ant-design/x';
import type { ConversationsProps } from '@ant-design/x';
import type { InputRef } from 'antd/es/input';
import { useChatStore } from '../../stores/chatStore';

type ConversationItem = NonNullable<ConversationsProps['items']>[number];

const GROUP_ORDER: Record<string, number> = {
  '置顶': 0,
  '今天': 1,
  '最近 7 天': 2,
  '更早': 3,
};

function getTimeGroup(updatedAt: string): string {
  const now = new Date();
  const updated = new Date(updatedAt);
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const weekStart = new Date(todayStart.getTime() - 6 * 24 * 60 * 60 * 1000);
  if (updated >= todayStart) return '今天';
  if (updated >= weekStart) return '最近 7 天';
  return '更早';
}

export default function ConversationSidebar(): JSX.Element {
  const {
    conversations,
    activeConversationId,
    createConversation,
    switchConversation,
    renameConversation,
    deleteConversation,
    clearConversation,
    togglePin,
  } = useChatStore();

  const [search, setSearch] = useState('');
  const [renameTargetId, setRenameTargetId] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const renameInputRef = useRef<InputRef>(null);

  const renameTarget = renameTargetId
    ? conversations.find((conversation) => conversation.id === renameTargetId)
    : undefined;
  const canSubmitRename = renameTitle.trim().length > 0;

  useEffect(() => {
    if (!renameTargetId) return;
    window.setTimeout(() => {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }, 0);
  }, [renameTargetId]);

  const filtered = search.trim()
    ? conversations.filter((c) => c.title.toLowerCase().includes(search.trim().toLowerCase()))
    : conversations;

  const items: ConversationItem[] = filtered.map((c) => ({
    key: c.id,
    label: c.title,
    group: c.pinned ? '置顶' : getTimeGroup(c.updatedAt),
    timestamp: new Date(c.updatedAt).getTime(),
  }));

  const handleActiveChange = (key: string) => {
    switchConversation(key);
  };

  const openRenameModal = (id: string) => {
    const current = conversations.find((c) => c.id === id);
    setRenameTargetId(id);
    setRenameTitle(current?.title ?? '');
  };

  const closeRenameModal = () => {
    setRenameTargetId(null);
    setRenameTitle('');
  };

  const submitRename = () => {
    if (!renameTargetId || !canSubmitRename) return;
    renameConversation(renameTargetId, renameTitle.trim());
    closeRenameModal();
  };

  return (
    <Flex vertical style={{ height: '100%', overflow: 'hidden' }}>
      {/* 品牌 logo */}
      <Flex align="center" gap={8} style={{ padding: '16px 16px 12px' }}>
        <RocketOutlined style={{ fontSize: 20, color: '#1677ff' }} />
        <Typography.Text strong style={{ fontSize: 15, letterSpacing: '-0.01em' }}>
          AstraCoreAI
        </Typography.Text>
      </Flex>

      {/* 新建会话 + 搜索 */}
      <Flex vertical gap={10} style={{ padding: '0 12px 10px' }}>
        <Button
          type="primary"
          icon={<EditOutlined />}
          block
          size="large"
          onClick={() => createConversation()}
        >
          新建会话
        </Button>
        <Input.Search
          placeholder="搜索会话"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
        />
      </Flex>

      {/* 会话列表 */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 4px' }}>
        {filtered.length === 0 ? (
          <Typography.Text
            type="secondary"
            style={{ fontSize: 13, display: 'block', textAlign: 'center', padding: '16px 8px' }}
          >
            {search ? '无匹配结果' : '暂无会话'}
          </Typography.Text>
        ) : (
          <Conversations
            groupable={{ sort: (a, b) => (GROUP_ORDER[a] ?? 99) - (GROUP_ORDER[b] ?? 99) }}
            items={items}
            activeKey={activeConversationId}
            onActiveChange={handleActiveChange}
            menu={(item: ConversationItem) => {
              const conv = conversations.find((c) => c.id === item.key);
              return {
                items: [
                  { key: 'pin', label: conv?.pinned ? '取消置顶' : '置顶' },
                  { key: 'rename', label: '重命名' },
                  { key: 'clear', label: '清空消息' },
                  { key: 'delete', label: '删除', danger: true },
                ],
                onClick: ({ key }: { key: string }) => {
                  const id = String(item.key);
                  if (key === 'pin') togglePin(id);
                  if (key === 'rename') {
                    openRenameModal(id);
                  }
                  if (key === 'clear') clearConversation(id);
                  if (key === 'delete') deleteConversation(id);
                },
              };
            }}
          />
        )}
      </div>
      <Modal
        title="重命名会话"
        open={Boolean(renameTargetId)}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ disabled: !canSubmitRename }}
        onOk={submitRename}
        onCancel={closeRenameModal}
        destroyOnHidden
      >
        <Flex vertical gap={8} style={{ paddingTop: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            给当前会话起一个更容易识别的名字。
          </Typography.Text>
          <Input
            ref={renameInputRef}
            value={renameTitle}
            maxLength={24}
            showCount
            placeholder={renameTarget?.title || '请输入新标题'}
            onChange={(event) => setRenameTitle(event.target.value)}
            onPressEnter={submitRename}
          />
        </Flex>
      </Modal>
    </Flex>
  );
}
