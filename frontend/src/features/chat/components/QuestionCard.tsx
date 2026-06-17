import { useState } from 'react';
import { Button, Checkbox, Flex, Input, Radio, Space, Typography, theme } from 'antd';
import { QuestionCircleOutlined } from '@ant-design/icons';
import type { PendingQuestion } from '@/shared/types/api';

type Props = {
  question: PendingQuestion;
  onSubmit: (selected: string[], freeform?: string | null) => Promise<void>;
};

export default function QuestionCard({ question, onSubmit }: Props): JSX.Element {
  const { token } = theme.useToken();
  const isDark = token.colorBgBase < '#888888';

  const [selected, setSelected] = useState<string[]>([]);
  const [freeform, setFreeform] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const canSubmit = selected.length > 0 || (question.allow_freeform && freeform.trim().length > 0);
  const submitButtonActive = canSubmit || submitted;

  const handleSubmit = async () => {
    if (!canSubmit || submitting || submitted) return;
    setSubmitting(true);
    try {
      await onSubmit(selected, question.allow_freeform ? freeform.trim() || null : null);
      setSubmitted(true);
    } finally {
      setSubmitting(false);
    }
  };

  const borderColor = isDark ? token.colorBorderSecondary : '#d6b4fc';
  const bg = isDark ? token.colorFillQuaternary : '#fdf8ff';
  const accentColor = '#722ed1';
  const headerBg = isDark ? token.colorFillTertiary : '#f3e8ff';

  return (
    <div
      style={{
        border: `1px solid ${borderColor}`,
        borderRadius: 12,
        background: bg,
        overflow: 'hidden',
        marginBottom: 12,
      }}
    >
      {/* header */}
      <Flex
        align="center"
        gap={8}
        style={{ padding: '10px 16px', background: headerBg, borderBottom: `1px solid ${borderColor}` }}
      >
        <QuestionCircleOutlined style={{ color: accentColor, fontSize: 14 }} />
        <Typography.Text style={{ fontSize: 13, fontWeight: 600, color: accentColor }}>
          {question.header || '需要你的输入'}
        </Typography.Text>
      </Flex>

      {/* body */}
      <div style={{ padding: '14px 16px' }}>
        <Typography.Text style={{ fontSize: 14, lineHeight: 1.6, display: 'block', marginBottom: 14 }}>
          {question.question}
        </Typography.Text>

        {question.options.length > 0 && (
          <div style={{ marginBottom: question.allow_freeform ? 12 : 0 }}>
            {question.multi_select ? (
              <Checkbox.Group
                value={selected}
                onChange={(vals) => setSelected(vals as string[])}
                disabled={submitted || submitting}
                style={{ width: '100%' }}
              >
                <Space direction="vertical" style={{ width: '100%' }}>
                  {question.options.map((opt) => (
                    <Checkbox key={opt.label} value={opt.label} style={{ fontSize: 13 }}>
                      <span>{opt.label}</span>
                      {opt.description && (
                        <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
                          {opt.description}
                        </Typography.Text>
                      )}
                    </Checkbox>
                  ))}
                </Space>
              </Checkbox.Group>
            ) : (
              <Radio.Group
                value={selected[0] ?? null}
                onChange={(e) => setSelected([e.target.value as string])}
                disabled={submitted || submitting}
                style={{ width: '100%' }}
              >
                <Space direction="vertical" style={{ width: '100%' }}>
                  {question.options.map((opt) => (
                    <Radio key={opt.label} value={opt.label} style={{ fontSize: 13 }}>
                      <span>{opt.label}</span>
                      {opt.description && (
                        <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
                          {opt.description}
                        </Typography.Text>
                      )}
                    </Radio>
                  ))}
                </Space>
              </Radio.Group>
            )}
          </div>
        )}

        {question.allow_freeform && (
          <Input.TextArea
            placeholder="或在此输入自定义回复..."
            value={freeform}
            onChange={(e) => setFreeform(e.target.value)}
            disabled={submitted || submitting}
            autoSize={{ minRows: 2, maxRows: 5 }}
            style={{ marginTop: question.options.length > 0 ? 0 : 0, fontSize: 13 }}
          />
        )}

        <Flex justify="flex-end" style={{ marginTop: 14 }}>
          <Button
            type={submitButtonActive ? 'primary' : 'default'}
            size="small"
            loading={submitting}
            disabled={!canSubmit || submitted}
            onClick={() => { void handleSubmit(); }}
            style={{
              borderRadius: 20,
              fontSize: 12,
              height: 28,
              padding: '0 16px',
              ...(submitButtonActive
                ? {
                    background: submitted ? token.colorSuccess : accentColor,
                    borderColor: submitted ? token.colorSuccess : accentColor,
                  }
                : {}),
            }}
          >
            {submitted ? '已提交' : '提交'}
          </Button>
        </Flex>
      </div>
    </div>
  );
}
