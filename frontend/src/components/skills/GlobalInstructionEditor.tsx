import { useState } from 'react';
import { Button, Card, Flex, Form, Select, Typography } from 'antd';
import RagMarkdownEditor from '../rag/RagMarkdownEditor';
import type { Skill, UserSettings } from '../../types/skill';

type Props = {
  value: string;
  defaultSkillId: string;
  skills: Skill[];
  onSave: (patch: Partial<UserSettings>) => Promise<void>;
};

export default function GlobalInstructionEditor({ value, defaultSkillId, skills, onSave }: Props): JSX.Element {
  const [instruction, setInstruction] = useState(value);
  const [skillId, setSkillId] = useState(defaultSkillId);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave({ global_instruction: instruction, default_skill_id: skillId });
    } finally {
      setSaving(false);
    }
  };

  const skillOptions = [
    { value: '', label: '无默认（每次手动选择）' },
    ...skills.map((s) => ({ value: s.id, label: s.name })),
  ];

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
        <Form.Item label="默认 Skill" style={{ marginBottom: 12 }}>
          <Select
            value={skillId}
            onChange={setSkillId}
            options={skillOptions}
            style={{ maxWidth: 320 }}
            placeholder="不设置默认 Skill"
          />
        </Form.Item>
        <Form.Item label="全局附加指令" style={{ marginBottom: 0 }}>
          <Flex vertical gap={4}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              追加到每次对话的 System Prompt 末尾，对所有 Skill 生效
            </Typography.Text>
            <RagMarkdownEditor value={instruction} onChange={setInstruction} height={160} />
          </Flex>
        </Form.Item>
      </Form>
    </Card>
  );
}
