import { useState, useEffect } from 'react';
import { Avatar, Button, Flex, Tooltip, Typography, theme } from 'antd';
import { Bubble } from '@ant-design/x';
import {
  CheckOutlined,
  CopyOutlined,
  DeleteOutlined,
  FilePdfOutlined,
  PictureOutlined,
  RobotOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { AttachmentPreview } from '@/features/attachments/types';
import type { ChatMessage } from '@/features/chat/types';
import { downloadAttachment } from '@/features/attachments/attachmentService';
import { useChatStore } from '@/features/chat/store/chatStore';
import { useSkillStore } from '@/features/skills/store/skillStore';
import { formatAppMessageTime } from '@/shared/utils/time';
import { formatBytes } from '@/shared/utils/format';
import { TTSButton } from '@/features/tts/TTSButton';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolActivityRow, SubAgentPanel } from './ToolActivity';
import MarkdownContent from './MarkdownContent';

function MessageActions({
  message,
  conversationId,
  visible,
}: {
  message: ChatMessage;
  conversationId: string;
  visible: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const { deleteMessage } = useChatStore();
  const timezone = useSkillStore((s) => s.settings.timezone);
  const { token } = theme.useToken();

  const handleCopy = () => {
    void navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const btnStyle: React.CSSProperties = {
    width: 28,
    height: 28,
    borderRadius: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: token.colorTextTertiary,
    fontSize: 13,
  };

  return (
    <Flex
      vertical
      style={{
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? 'auto' : 'none',
        transition: 'opacity 0.15s ease',
        padding: '2px 0',
      }}
    >
      <Flex gap={2} align="center">
        <Tooltip title={copied ? '已复制' : '复制'}>
          <Button
            type="text"
            size="small"
            icon={copied ? <CheckOutlined style={{ color: token.colorSuccess }} /> : <CopyOutlined />}
            onClick={handleCopy}
            style={btnStyle}
          />
        </Tooltip>
        <Tooltip title="删除">
          <Button
            type="text"
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => deleteMessage(conversationId, message.id)}
            style={{ ...btnStyle, color: token.colorError }}
          />
        </Tooltip>
        {message.role === 'assistant' && message.content && (
          <TTSButton messageId={message.id} content={message.content} btnStyle={btnStyle} />
        )}
      </Flex>
      <span style={{
        fontSize: 11,
        color: token.colorTextQuaternary,
        padding: '0 4px',
        userSelect: 'none',
        whiteSpace: 'nowrap',
      }}>
        {formatAppMessageTime(message.createdAt, timezone)}
      </span>
    </Flex>
  );
}

function SentAttachmentPreview({ att }: { att: AttachmentPreview }) {
  const { token } = theme.useToken();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const isImage = att.mimeType.startsWith('image/');
  const isPdf = att.mimeType === 'application/pdf';

  useEffect(() => {
    if (!isImage) return undefined;
    let revoked = false;
    let objectUrl: string | null = null;

    void downloadAttachment(att.id)
      .then((blob) => {
        if (revoked) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      })
      .catch(() => {
        if (!revoked) setPreviewUrl(null);
      });

    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [att.id, isImage]);

  return (
    <Flex
      align="center"
      gap={8}
      onClick={() => { if (previewUrl) window.open(previewUrl, '_blank', 'noopener,noreferrer'); }}
      style={{
        width: 220,
        padding: 6,
        borderRadius: 12,
        cursor: previewUrl ? 'zoom-in' : 'default',
        background: 'rgba(255, 255, 255, 0.72)',
        border: `1px solid ${token.colorBorderSecondary}`,
        boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.72)',
      }}
    >
      {previewUrl ? (
        <img
          src={previewUrl}
          alt={att.filename}
          style={{ width: 44, height: 44, objectFit: 'cover', borderRadius: 9, flexShrink: 0 }}
        />
      ) : (
        <Flex
          align="center"
          justify="center"
          style={{
            width: 44,
            height: 44,
            borderRadius: 9,
            flexShrink: 0,
            color: isPdf ? token.colorError : token.colorTextSecondary,
            background: token.colorFillSecondary,
            fontSize: 20,
          }}
        >
          {isPdf ? <FilePdfOutlined /> : <PictureOutlined />}
        </Flex>
      )}
      <Flex vertical gap={2} style={{ minWidth: 0, flex: 1 }}>
        <Typography.Text ellipsis style={{ fontSize: 12, lineHeight: 1.25 }}>
          {att.filename}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11, lineHeight: 1.2 }}>
          {isImage ? '图片' : isPdf ? 'PDF' : '附件'} · {formatBytes(att.sizeBytes)}
        </Typography.Text>
      </Flex>
    </Flex>
  );
}

function SentAttachmentList({ attachments }: { attachments: AttachmentPreview[] }) {
  return (
    <Flex wrap gap={6} justify="flex-end" style={{ marginTop: 8, maxWidth: 460 }}>
      {attachments.map((att) => (
        <SentAttachmentPreview key={att.id} att={att} />
      ))}
    </Flex>
  );
}

function AssistantContent({ message }: { message: ChatMessage }) {
  const blocks = message.thinkingBlocks ?? [];
  const isStreaming = message.status === 'streaming';
  const mode = message.thinkingMode ?? (message.toolActivity?.length ? 'tool' : 'normal');
  const collapseMode = useSkillStore((s) => s.settings.thinking_collapse_mode);

  // 只渲染有内容的块，或最后一个正在流式生成的块（内容还没来）
  const visible = blocks
    .map((block, idx) => {
      const isLast = idx === blocks.length - 1;
      const streaming = isStreaming && isLast && !message.content;
      return { block, idx, streaming };
    })
    .filter(({ block, streaming }) => block.trim().length > 0 || streaming);

  const multiRound = visible.length > 1;

  return (
    <div style={{ width: '100%' }}>
      {visible.map(({ block, idx, streaming }, renderedIdx) => (
        <ThinkingBlock
          key={idx}
          thinking={block}
          streaming={streaming}
          roundLabel={multiRound ? `第 ${renderedIdx + 1} 轮思考` : undefined}
          mode={mode}
          collapseMode={collapseMode}
        />
      ))}
      {message.toolActivity && message.toolActivity.length > 0 && (
        <ToolActivityRow tools={message.toolActivity} />
      )}
      {message.subAgents && message.subAgents.length > 0 && (
        <SubAgentPanel agents={message.subAgents} />
      )}
      <MarkdownContent content={message.content} isStreaming={isStreaming} />
    </div>
  );
}

export function MessageRow({
  message,
  conversationId,
  hoveredMsgId,
  onMsgEnter,
  onMsgLeave,
}: {
  message: ChatMessage;
  conversationId: string;
  hoveredMsgId: string | null;
  onMsgEnter: (id: string) => void;
  onMsgLeave: () => void;
}) {
  const { token } = theme.useToken();
  const isUser = message.role === 'user';
  const isLoading =
    message.status === 'streaming' &&
    message.content.length === 0 &&
    !message.thinkingBlocks?.length;
  const actionsVisible = hoveredMsgId === message.id && message.status !== 'streaming';

  return (
    <div
      style={{ display: 'flex', alignItems: 'flex-start', marginBottom: 16 }}
      onMouseEnter={() => onMsgEnter(message.id)}
      onMouseLeave={onMsgLeave}
    >
      {/* AI 头像槽 — 用户消息时为空占位，保持三栏结构稳定 */}
      <div style={{ width: 44, flexShrink: 0, paddingRight: 12 }}>
        {!isUser && (
          <Avatar
            icon={<RobotOutlined />}
            size={32}
            style={{ background: '#722ed1', marginTop: 2 }}
          />
        )}
      </div>

      {/* 内容槽 */}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: isUser ? 'flex-end' : 'flex-start',
        }}
      >
        {isLoading ? (
          <Bubble loading content="" variant="borderless" />
        ) : isUser ? (
          <Flex vertical align="flex-end" style={{ maxWidth: '80%' }}>
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 12,
                background: token.colorFillSecondary,
                wordBreak: 'break-word',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.6,
                fontSize: 14,
              }}
            >
              {message.content}
            </div>
            {message.attachments && message.attachments.length > 0 && (
              <SentAttachmentList attachments={message.attachments} />
            )}
          </Flex>
        ) : (
          <AssistantContent message={message} />
        )}

        <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginTop: -4 }}>
          <MessageActions message={message} conversationId={conversationId} visible={actionsVisible} />
        </div>
      </div>

      {/* 用户头像槽 — AI 消息时为空占位 */}
      <div style={{ width: 44, flexShrink: 0, paddingLeft: 12, display: 'flex', justifyContent: 'flex-end' }}>
        {isUser && (
          <Avatar
            icon={<UserOutlined />}
            size={32}
            style={{ background: '#1677ff', marginTop: 2 }}
          />
        )}
      </div>
    </div>
  );
}
