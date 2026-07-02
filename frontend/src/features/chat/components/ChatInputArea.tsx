import { useState, useRef, useCallback } from 'react';
import { Sender } from '@ant-design/x';
import {
  DatabaseOutlined,
  GlobalOutlined,
  PaperClipOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  ToolOutlined,
} from '@ant-design/icons';
import { Button, Drawer, Flex, Grid, Popover, Slider, Tooltip, Typography, theme } from 'antd';
import { useChatStore } from '@/features/chat/store/chatStore';
import { useSystemStore } from '@/features/system/store/systemStore';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { useAttachmentUpload } from '@/features/chat/hooks/useAttachmentUpload';
import { ThinkingModeSelector } from './ThinkingBlock';
import { ReasoningEffortSelector } from './ReasoningEffortSelector';
import ModelSelector from './ModelSelector';
import TokenUsageBar from './TokenUsageBar';
import AttachmentPreviewCard from '@/features/attachments/components/AttachmentPreviewCard';
import ImagePreviewModal from '@/features/attachments/components/ImagePreviewModal';
import type {
  TemperatureControl,
  ThinkingControl,
  ReasoningEffortControl,
  TopPControl,
  TopKControl,
} from '@/features/system/types';

const { useBreakpoint } = Grid;

const PARAM_HELP: Record<'temperature' | 'top_p' | 'top_k', { title: string; text: string; tip: string }> = {
  temperature: {
    title: 'Temperature 是什么？',
    text: '控制回答的随机性。值越低越稳定、越按常规回答；值越高越发散、更有创意，但也更容易跑偏。',
    tip: '建议：日常问答/代码 0.2-0.7；创意写作 0.8-1.2。调整它会清空 Top-P 和 Top-K。',
  },
  top_p: {
    title: 'Top-P 是什么？',
    text: '控制模型只从累计概率最高的一批词里选择。值越低越保守，值越高选择范围越大。',
    tip: '建议：一般保持 0.9-1.0。调整它会清空 Temperature 和 Top-K。',
  },
  top_k: {
    title: 'Top-K 是什么？',
    text: '限制每一步只从概率最高的 K 个候选词里选。K 越小越保守，K 越大越开放。',
    tip: '建议：不确定就用默认值；想更稳定可降低，想更开放可提高。调整它会清空 Temperature 和 Top-P。',
  },
};

function ParamLabel({ kind, label }: { kind: keyof typeof PARAM_HELP; label: string }) {
  const help = PARAM_HELP[kind];
  return (
    <Flex align="center" gap={4} style={{ width: 92, flexShrink: 0 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {label}
      </Typography.Text>
      <Tooltip
        title={(
          <Flex vertical gap={4}>
            <Typography.Text style={{ color: 'inherit', fontSize: 12, fontWeight: 600 }}>{help.title}</Typography.Text>
            <Typography.Text style={{ color: 'inherit', fontSize: 12 }}>{help.text}</Typography.Text>
            <Typography.Text style={{ color: 'inherit', fontSize: 12 }}>{help.tip}</Typography.Text>
          </Flex>
        )}
      >
        <QuestionCircleOutlined style={{ fontSize: 12, color: '#8c8c8c' }} />
      </Tooltip>
    </Flex>
  );
}

function SamplingRow({
  active,
  children,
}: {
  active: boolean;
  children: React.ReactNode;
}) {
  const { token } = theme.useToken();
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '92px minmax(96px, 1fr) 40px 48px',
        alignItems: 'center',
        columnGap: 10,
        padding: '8px 10px',
        borderRadius: 12,
        borderLeft: `3px solid ${active ? '#60a5fa' : 'rgba(148, 163, 184, 0.42)'}`,
        background: active ? 'rgba(59, 130, 246, 0.08)' : 'rgba(148, 163, 184, 0.08)',
        transition: 'background 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease',
        boxSizing: 'border-box',
      }}
    >
      {children}
      <span
        style={{
          justifySelf: 'end',
          width: 44,
          textAlign: 'center',
          fontSize: 11,
          lineHeight: '18px',
          borderRadius: 999,
          color: active ? '#1d4ed8' : token.colorTextSecondary,
          background: active ? '#e0efff' : 'rgba(148, 163, 184, 0.14)',
          border: `1px solid ${active ? '#bfdbfe' : 'rgba(148, 163, 184, 0.24)'}`,
        }}
      >
        {active ? '生效' : '待用'}
      </span>
    </div>
  );
}

function AdvancedPanel({
  temperatureControl,
  topPControl,
  topKControl,
  temperature,
  topP,
  topK,
  onTemperatureChange,
  onTopPChange,
  onTopKChange,
  disabled,
}: {
  temperatureControl: TemperatureControl | undefined;
  topPControl: TopPControl | undefined;
  topKControl: TopKControl | undefined;
  temperature: number | null;
  topP: number | null;
  topK: number | null;
  onTemperatureChange: (v: number | null) => void;
  onTopPChange: (v: number | null) => void;
  onTopKChange: (v: number | null) => void;
  disabled: boolean;
}) {
  const { token } = theme.useToken();
  const tempValue = temperature ?? temperatureControl?.profile_default ?? 0.7;
  // 当用户未手动设置时，fallback 到 profile_default；profile 也未配置则用 1.0（各厂商默认值）
  const topPValue = topP ?? topPControl?.profile_default ?? 1.0;
  const topKValue = topK ?? (topKControl ? 50 : 0);
  const activeSampling: 'temperature' | 'top_p' | 'top_k' =
    topP !== null ? 'top_p' : topK !== null ? 'top_k' : 'temperature';

  return (
    <Flex
      vertical
      gap={12}
      style={{
        width: 'min(480px, calc(100vw - 32px))',
        maxWidth: 'calc(100vw - 32px)',
        padding: '4px 0',
      }}
    >
      <Flex vertical gap={2}>
        <Typography.Text strong style={{ fontSize: 13 }}>高级生成参数</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          采样参数三选一：当前标记为「生效」的参数会发送给模型。
        </Typography.Text>
      </Flex>

      {temperatureControl && (
        <SamplingRow active={activeSampling === 'temperature'}>
          <ParamLabel kind="temperature" label="Temperature" />
          <Slider
            disabled={disabled}
            min={temperatureControl.min}
            max={temperatureControl.max}
            step={temperatureControl.step}
            value={tempValue}
            onChange={onTemperatureChange}
            style={{ flex: 1, minWidth: 96 }}
            tooltip={{ formatter: (v) => v?.toFixed(2) }}
          />
          <Typography.Text style={{ fontSize: 12, width: 36, textAlign: 'right', flexShrink: 0 }}>
            {tempValue.toFixed(2)}
          </Typography.Text>
        </SamplingRow>
      )}

      {topPControl && (
        <SamplingRow active={activeSampling === 'top_p'}>
          <ParamLabel kind="top_p" label="Top-P" />
          <Slider
            disabled={disabled}
            min={topPControl.min}
            max={topPControl.max}
            step={topPControl.step}
            value={topPValue}
            onChange={onTopPChange}
            style={{ flex: 1, minWidth: 96 }}
            tooltip={{ formatter: (v) => v?.toFixed(2) }}
          />
          <Typography.Text style={{ fontSize: 12, width: 36, textAlign: 'right', flexShrink: 0 }}>
            {topPValue.toFixed(2)}
          </Typography.Text>
        </SamplingRow>
      )}
      {topKControl && (
        <SamplingRow active={activeSampling === 'top_k'}>
          <ParamLabel kind="top_k" label="Top-K" />
          <Slider
            disabled={disabled}
            min={topKControl.min}
            max={topKControl.max}
            step={topKControl.step}
            value={topKValue}
            onChange={onTopKChange}
            style={{ flex: 1, minWidth: 96 }}
            tooltip={{ formatter: (v) => String(v) }}
          />
          <Typography.Text style={{ fontSize: 12, width: 36, textAlign: 'right', flexShrink: 0 }}>
            {topKValue}
          </Typography.Text>
        </SamplingRow>
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11, color: token.colorTextTertiary }}>
        小提示：代码、事实问答偏低随机性；头脑风暴、写作可适当提高随机性。不确定就保持默认。
      </Typography.Text>
    </Flex>
  );
}

export function ChatInputArea({ onSendMessage }: { onSendMessage: (value: string) => void }) {
  const { token } = theme.useToken();
  const screens = useBreakpoint();
  const appTheme = useSettingsStore((s) => s.theme);
  const [inputValue, setInputValue] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<{ src: string; alt: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    activeConversationId,
    activeModelId,
    messagesByConversation,
    pendingQuestionByConversation,
    thinkingMode,
    reasoningEffort,
    temperature,
    topP,
    topK,
    enableRag,
    enableTools,
    enableWeb,
    latestUsageByConversation,
    pendingAttachments,
    setThinkingMode,
    setReasoningEffort,
    setTemperature,
    setTopP,
    setTopK,
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

  // Derive control descriptors from active profile
  const controls = activeProfile?.controls ?? [];
  const thinkingControl = controls.find((c) => c.kind === 'thinking') as ThinkingControl | undefined;
  const reasoningControl = controls.find((c) => c.kind === 'reasoning_effort') as ReasoningEffortControl | undefined;
  const temperatureControl = controls.find((c) => c.kind === 'temperature') as TemperatureControl | undefined;
  const topPControl = controls.find((c) => c.kind === 'top_p') as TopPControl | undefined;
  const topKControl = controls.find((c) => c.kind === 'top_k') as TopKControl | undefined;
  const hasAdvanced = !!(temperatureControl ?? topPControl ?? topKControl);

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
    setImagePreview(null);
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
  const isDark = appTheme === 'dark';
  const isMobile = screens.md === false;
  const advancedPanel = (
    <AdvancedPanel
      temperatureControl={temperatureControl}
      topPControl={topPControl}
      topKControl={topKControl}
      temperature={temperature}
      topP={topP}
      topK={topK}
      onTemperatureChange={setTemperature}
      onTopPChange={setTopP}
      onTopKChange={setTopK}
      disabled={toolbarDisabled}
    />
  );

  return (
    <div
      style={{ padding: '8px 0 20px', borderTop: '1px solid rgba(5, 5, 5, 0.06)', flexShrink: 0 }}
    >
      <div
        style={{
          maxWidth: 'var(--content-max-width)',
          margin: '0 auto',
          width: '100%',
          padding: '0 var(--content-padding-x)',
          position: 'relative',
        }}
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
              inset: '0 var(--content-padding-x) 0',
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
          {thinkingControl && (
            <ThinkingModeSelector
              value={thinkingMode}
              disabled={toolbarDisabled}
              onChange={setThinkingMode}
              modes={thinkingControl.modes}
            />
          )}

          {reasoningControl && (
            <ReasoningEffortSelector
              value={reasoningEffort}
              levels={reasoningControl.levels}
              defaultValue={reasoningControl.default}
              disabled={toolbarDisabled}
              onChange={setReasoningEffort}
            />
          )}

          {hasAdvanced && !isMobile && (
            <Popover
              trigger="click"
              placement="topLeft"
              open={advancedOpen}
              onOpenChange={(open) => setAdvancedOpen(open)}
              overlayStyle={{ maxWidth: 'calc(100vw - 16px)' }}
              overlayInnerStyle={{ borderRadius: 14, padding: 12, maxWidth: 'calc(100vw - 16px)' }}
              content={advancedPanel}
            >
              <Button
                size="small"
                type={advancedOpen ? 'primary' : 'default'}
                ghost={advancedOpen}
                disabled={toolbarDisabled}
                style={{
                  borderRadius: 20,
                  fontSize: 12,
                  height: 26,
                  padding: '0 10px',
                  ...(advancedOpen
                    ? { borderColor: '#6366f1', color: '#6366f1', background: '#eef2ff' }
                    : { color: token.colorTextSecondary }),
                }}
                icon={<RobotOutlined />}
              >
                高级
              </Button>
            </Popover>
          )}

          {hasAdvanced && isMobile && (
            <>
              <Button
                size="small"
                type={advancedOpen ? 'primary' : 'default'}
                ghost={advancedOpen}
                disabled={toolbarDisabled}
                onClick={() => setAdvancedOpen(true)}
                style={{
                  borderRadius: 20,
                  fontSize: 12,
                  height: 26,
                  padding: '0 10px',
                  ...(advancedOpen
                    ? { borderColor: '#6366f1', color: '#6366f1', background: '#eef2ff' }
                    : { color: token.colorTextSecondary }),
                }}
                icon={<RobotOutlined />}
              >
                高级
              </Button>
              <Drawer
                open={advancedOpen}
                placement="bottom"
                onClose={() => setAdvancedOpen(false)}
                height="auto"
                styles={{
                  content: {
                    borderRadius: '18px 18px 0 0',
                    overflow: 'hidden',
                  },
                  body: {
                    padding: 14,
                    maxHeight: '72vh',
                    overflowY: 'auto',
                  },
                }}
              >
                {advancedPanel}
              </Drawer>
            </>
          )}

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
                borderColor: isAttachmentButtonDisabled
                  ? (isDark ? 'rgba(148, 163, 184, 0.36)' : token.colorBorderSecondary)
                  : isAttachmentButtonActive
                    ? '#2f80ed'
                    : (isDark ? 'rgba(96, 165, 250, 0.30)' : 'rgba(47, 84, 235, 0.18)'),
                color: isAttachmentButtonDisabled
                  ? (isDark ? 'rgba(226, 232, 240, 0.62)' : token.colorTextDisabled)
                  : isAttachmentButtonActive
                    ? '#1d4ed8'
                    : token.colorTextSecondary,
                background: isAttachmentButtonDisabled
                  ? (isDark ? 'rgba(30, 41, 59, 0.72)' : token.colorBgContainerDisabled)
                  : isAttachmentButtonActive
                    ? (isDark
                        ? 'linear-gradient(135deg, rgba(29, 78, 216, 0.30) 0%, rgba(15, 23, 42, 0.86) 100%)'
                        : 'linear-gradient(135deg, #eef6ff 0%, #f7fbff 100%)')
                    : (isDark
                        ? 'linear-gradient(135deg, rgba(15, 23, 42, 0.88) 0%, rgba(30, 41, 59, 0.72) 100%)'
                        : 'linear-gradient(135deg, rgba(245, 248, 255, 0.95) 0%, rgba(250, 252, 255, 0.9) 100%)'),
                boxShadow: isAttachmentButtonDisabled
                  ? (isDark ? 'inset 0 1px 0 rgba(255, 255, 255, 0.05)' : 'none')
                  : isAttachmentButtonActive
                    ? '0 4px 12px rgba(47, 128, 237, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.9)'
                    : (isDark ? 'inset 0 1px 0 rgba(255, 255, 255, 0.06)' : 'inset 0 1px 0 rgba(255, 255, 255, 0.9)'),
              }}
              icon={<PaperClipOutlined />}
            >
              {pendingAttachments.length > 0 ? `附件 ${pendingAttachments.length}` : '附件'}
            </Button>
          </Tooltip>

          <ModelSelector disabled={toolbarDisabled} />

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
              <AttachmentPreviewCard
                key={att.id}
                attachment={att}
                imageUrl={previewUrls[att.id]}
                size="compact"
                onPreview={() => {
                  const src = previewUrls[att.id];
                  if (src) setImagePreview({ src, alt: att.filename });
                }}
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
      <ImagePreviewModal
        open={!!imagePreview}
        src={imagePreview?.src ?? null}
        alt={imagePreview?.alt ?? ''}
        onClose={() => setImagePreview(null)}
      />
    </div>
  );
}
