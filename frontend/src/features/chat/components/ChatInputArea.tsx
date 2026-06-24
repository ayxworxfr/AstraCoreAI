import { useState, useRef, useCallback } from 'react';
import { Sender } from '@ant-design/x';
import {
  DatabaseOutlined,
  DeleteOutlined,
  FilePdfOutlined,
  GlobalOutlined,
  PaperClipOutlined,
  PictureOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { Button, Flex, Tooltip, Typography, theme } from 'antd';
import { useChatStore } from '@/features/chat/store/chatStore';
import { useSystemStore } from '@/features/system/store/systemStore';
import { useAttachmentUpload } from '@/features/chat/hooks/useAttachmentUpload';
import { ThinkingModeSelector } from './ThinkingBlock';
import ModelSelector from './ModelSelector';
import TokenUsageBar from './TokenUsageBar';
import { formatBytes } from '@/shared/utils/format';
import type { AttachmentPreview } from '@/features/attachments/types';

function AttachmentChip({
  att,
  previewUrl,
  onRemove,
}: {
  att: AttachmentPreview;
  previewUrl?: string;
  onRemove: (id: string) => void;
}) {
  const { token } = theme.useToken();
  const isPdf = att.mimeType === 'application/pdf';
  const icon = isPdf ? <FilePdfOutlined /> : (previewUrl ? undefined : <PictureOutlined />);
  const name = att.filename.length > 20 ? `${att.filename.slice(0, 18)}…` : att.filename;
  return (
    <Flex
      align="center"
      gap={6}
      style={{
        padding: '4px 8px',
        borderRadius: 8,
        background: token.colorFillSecondary,
        border: `1px solid ${token.colorBorderSecondary}`,
        fontSize: 12,
        maxWidth: 180,
      }}
    >
      {previewUrl ? (
        <img
          src={previewUrl}
          alt={att.filename}
          style={{ width: 28, height: 28, objectFit: 'cover', borderRadius: 4, flexShrink: 0 }}
        />
      ) : (
        <span style={{ color: token.colorTextSecondary, fontSize: 16, flexShrink: 0 }}>{icon}</span>
      )}
      <Flex vertical gap={0} style={{ flex: 1, minWidth: 0 }}>
        <Typography.Text ellipsis style={{ fontSize: 12, lineHeight: 1.3 }}>{name}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11, lineHeight: 1.2 }}>{formatBytes(att.sizeBytes)}</Typography.Text>
      </Flex>
      <Button
        type="text"
        size="small"
        icon={<DeleteOutlined />}
        onClick={() => onRemove(att.id)}
        style={{ color: token.colorTextTertiary, padding: 0, width: 20, height: 20, flexShrink: 0 }}
      />
    </Flex>
  );
}

export function ChatInputArea({ onSendMessage }: { onSendMessage: (value: string) => void }) {
  const { token } = theme.useToken();
  const [inputValue, setInputValue] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    activeConversationId,
    activeModelId,
    messagesByConversation,
    pendingQuestionByConversation,
    thinkingMode,
    enableRag,
    enableTools,
    enableWeb,
    latestUsageByConversation,
    pendingAttachments,
    setThinkingMode,
    setEnableRag,
    setEnableTools,
    setEnableWeb,
    cancelStream,
  } = useChatStore();

  const { systemInfo } = useSystemStore();
  const ragEnabled = systemInfo?.rag_enabled ?? true;
  const llmInfo = systemInfo?.llm ?? null;

  const messages = messagesByConversation[activeConversationId] ?? [];
  const isStreaming = messages.some((m) => m.status === 'streaming');
  const pendingQuestion = pendingQuestionByConversation[activeConversationId] ?? null;
  const toolbarDisabled = isStreaming || !!pendingQuestion;
  const senderInputDisabled = !!pendingQuestion;

  const defaultProfile = llmInfo?.default_profile;
  const activeProfile = llmInfo?.profiles.find((p) => p.id === (activeModelId ?? defaultProfile));
  const visionCapable = activeProfile?.capabilities.vision ?? true;
  const baseAttachmentDisabled = toolbarDisabled || !visionCapable;

  const {
    previewUrls,
    uploadingCount,
    draggingFiles,
    clearPreviewUrls,
    handleFileChange,
    handlePaste,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handleRemoveAttachment,
  } = useAttachmentUpload({ attachmentDisabled: baseAttachmentDisabled });

  const isAttachmentButtonActive = pendingAttachments.length > 0 || uploadingCount > 0;

  const handleSend = useCallback((value: string) => {
    clearPreviewUrls();
    onSendMessage(value);
  }, [clearPreviewUrls, onSendMessage]);

  const handleSenderKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== 'Enter') return;
    if ((event.nativeEvent as KeyboardEvent).isComposing) return;

    if (event.ctrlKey) {
      event.preventDefault();
      const textarea = event.currentTarget as HTMLTextAreaElement;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const nextValue = `${inputValue.slice(0, start)}\n${inputValue.slice(end)}`;
      setInputValue(nextValue);
      window.requestAnimationFrame(() => {
        textarea.setSelectionRange(start + 1, start + 1);
      });
      return;
    }

    event.preventDefault();
    const message = inputValue.trim();
    if (!message) return;
    setInputValue('');
    handleSend(message);
  };

  const isAttachmentButtonDisabled = baseAttachmentDisabled || uploadingCount > 0;

  return (
    <div
      style={{ padding: '8px 0 20px', borderTop: '1px solid rgba(5, 5, 5, 0.06)', flexShrink: 0 }}
    >
      <div
        style={{ maxWidth: 860, margin: '0 auto', width: '100%', padding: '0 24px', position: 'relative' }}
        onPaste={handlePaste}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {draggingFiles && (
          <Flex
            align="center"
            justify="center"
            style={{
              position: 'absolute',
              inset: '0 24px 0',
              zIndex: 20,
              borderRadius: 16,
              border: '1px dashed #2f80ed',
              color: '#1d4ed8',
              background: 'rgba(238, 246, 255, 0.88)',
              backdropFilter: 'blur(4px)',
              fontSize: 13,
              fontWeight: 600,
              pointerEvents: 'none',
            }}
          >
            松开即可上传图片或 PDF
          </Flex>
        )}

        {/* 工具栏独立一行，不占 Sender 内部空间 */}
        <Flex align="center" gap={6} style={{ marginBottom: 8, flexWrap: 'wrap' }}>
          <ThinkingModeSelector
            value={thinkingMode}
            disabled={toolbarDisabled}
            onChange={setThinkingMode}
          />

          {ragEnabled && (
            <Tooltip title={enableRag ? '关闭知识库检索' : '开启知识库检索（RAG）'}>
              <Button
                size="small"
                type={enableRag ? 'primary' : 'default'}
                ghost={enableRag}
                disabled={toolbarDisabled}
                onClick={() => setEnableRag(!enableRag)}
                style={{
                  borderRadius: 20,
                  fontSize: 12,
                  height: 26,
                  padding: '0 10px',
                  ...(enableRag ? { borderColor: '#1677ff', color: '#1677ff', background: '#e6f4ff' } : {}),
                }}
                icon={<DatabaseOutlined />}
              >
                知识库
              </Button>
            </Tooltip>
          )}

          <Tooltip title={enableTools ? '关闭工具调用（Agent 模式）' : '开启工具调用，AI 会多轮思考并使用工具'}>
            <Button
              size="small"
              type={enableTools ? 'primary' : 'default'}
              ghost={enableTools}
              disabled={toolbarDisabled}
              onClick={() => setEnableTools(!enableTools)}
              style={{
                borderRadius: 20,
                fontSize: 12,
                height: 26,
                padding: '0 10px',
                ...(enableTools ? { borderColor: '#fa8c16', color: '#fa8c16', background: '#fff7e6' } : {}),
              }}
              icon={<ToolOutlined />}
            >
              工具
            </Button>
          </Tooltip>

          <Tooltip title={enableWeb ? '关闭联网搜索' : '开启联网搜索'}>
            <Button
              size="small"
              type={enableWeb ? 'primary' : 'default'}
              ghost={enableWeb}
              disabled={toolbarDisabled}
              onClick={() => setEnableWeb(!enableWeb)}
              style={{
                borderRadius: 20,
                fontSize: 12,
                height: 26,
                padding: '0 10px',
                ...(enableWeb ? { borderColor: '#13c2c2', color: '#13c2c2', background: '#e6fffb' } : {}),
              }}
              icon={<GlobalOutlined />}
            >
              联网
            </Button>
          </Tooltip>

          <ModelSelector disabled={toolbarDisabled} />

          <Tooltip title={!visionCapable ? '当前模型不支持图片/文档附件' : '上传图片或 PDF'}>
            <Button
              size="small"
              type="default"
              disabled={isAttachmentButtonDisabled}
              loading={uploadingCount > 0}
              onClick={() => fileInputRef.current?.click()}
              style={{
                borderRadius: 20,
                fontSize: 12,
                height: 26,
                padding: '0 12px',
                borderColor: isAttachmentButtonActive ? '#2f80ed' : 'rgba(47, 84, 235, 0.18)',
                color: isAttachmentButtonDisabled
                  ? token.colorTextDisabled
                  : isAttachmentButtonActive
                    ? '#1d4ed8'
                    : token.colorTextSecondary,
                background: isAttachmentButtonDisabled
                  ? token.colorBgContainerDisabled
                  : isAttachmentButtonActive
                    ? 'linear-gradient(135deg, #eef6ff 0%, #f7fbff 100%)'
                    : 'linear-gradient(135deg, rgba(245, 248, 255, 0.95) 0%, rgba(250, 252, 255, 0.9) 100%)',
                boxShadow: isAttachmentButtonDisabled
                  ? 'none'
                  : isAttachmentButtonActive
                    ? '0 4px 12px rgba(47, 128, 237, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.9)'
                    : 'inset 0 1px 0 rgba(255, 255, 255, 0.9)',
              }}
              icon={<PaperClipOutlined />}
            >
              {pendingAttachments.length > 0 ? `附件 ${pendingAttachments.length}` : '附件'}
            </Button>
          </Tooltip>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
            style={{ display: 'none' }}
            onChange={(e) => { void handleFileChange(e); }}
          />
        </Flex>

        {pendingAttachments.length > 0 && (
          <Flex wrap gap={6} style={{ marginBottom: 8 }}>
            {pendingAttachments.map((att) => (
              <AttachmentChip
                key={att.id}
                att={att}
                previewUrl={previewUrls[att.id]}
                onRemove={handleRemoveAttachment}
              />
            ))}
          </Flex>
        )}

        <Sender
          value={inputValue}
          onChange={setInputValue}
          loading={isStreaming}
          disabled={senderInputDisabled}
          submitType={false}
          onKeyDown={handleSenderKeyDown}
          onSubmit={(value) => {
            setInputValue('');
            handleSend(value);
          }}
          onCancel={() => cancelStream(activeConversationId)}
          placeholder={pendingQuestion ? '等待你回答上方问题...' : '输入问题，Enter 发送，Ctrl+Enter 换行'}
        />

        {/* Token 用量状态栏：位于输入框正下方，有数据时用自身 padding 撑起底部 */}
        {latestUsageByConversation[activeConversationId] && (
          <div style={{ marginBottom: -20 }}>
            <TokenUsageBar {...latestUsageByConversation[activeConversationId]} />
          </div>
        )}
      </div>
    </div>
  );
}
