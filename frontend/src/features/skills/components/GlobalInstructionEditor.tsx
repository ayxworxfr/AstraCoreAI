import { useEffect, useState } from 'react';
import { Button, Card, Flex, Form, Input, Typography, message } from 'antd';
import RagMarkdownEditor from '@/features/rag/components/RagMarkdownEditor';
import { normalizeError } from '@/shared/services/apiClient';
import type { UserSettings } from '@/features/skills/types';

type Props = {
  value: string;
  settings: UserSettings;
  onSave: (patch: Partial<UserSettings>) => Promise<void>;
};

export default function GlobalInstructionEditor({ value, settings, onSave }: Props): JSX.Element {
  const [instruction, setInstruction] = useState(value);
  const [aiName, setAiName] = useState(settings.ai_name);
  const [ownerName, setOwnerName] = useState(settings.owner_name);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setInstruction(value);
  }, [value]);

  useEffect(() => {
    setAiName(settings.ai_name);
  }, [settings.ai_name]);

  useEffect(() => {
    setOwnerName(settings.owner_name);
  }, [settings.owner_name]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave({
        global_instruction: instruction,
        ai_name: aiName,
        owner_name: ownerName,
      });
      void message.success('全局设置已保存');
    } catch (error) {
      void message.error(normalizeError(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      size="small"
      title={<Typography.Text strong>全局设置</Typography.Text>}
      extra={
        <Button type="primary" size="small" loading={saving} onClick={handleSave}>
          保存
        </Button>
      }
    >
      <Form layout="vertical">
        <Flex gap={12} style={{ marginBottom: 12 }}>
          <Form.Item label="AI 名称" style={{ flex: 1, marginBottom: 0 }}>
            <Input
              value={aiName}
              onChange={(e) => setAiName(e.target.value)}
              placeholder="小卡"
              maxLength={20}
            />
          </Form.Item>
          <Form.Item label="主人名称" style={{ flex: 1, marginBottom: 0 }}>
            <Input
              value={ownerName}
              onChange={(e) => setOwnerName(e.target.value)}
              placeholder="留空则显示「用户」"
              maxLength={20}
            />
          </Form.Item>
        </Flex>
        <Form.Item label="全局附加指令" style={{ marginBottom: 0 }}>
          <Flex vertical gap={4}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              追加到每次对话的 System Prompt 末尾，对所有技能生效
            </Typography.Text>
            <RagMarkdownEditor value={instruction} onChange={setInstruction} height={180} />
          </Flex>
        </Form.Item>
      </Form>
    </Card>
  );
}
