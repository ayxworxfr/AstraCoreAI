import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Flex, Form, Input, Select, Typography } from 'antd';
import RagMarkdownEditor from '../rag/RagMarkdownEditor';
import type { Skill, UserSettings } from '../../types/skill';

type Props = {
  value: string;
  defaultSkillId: string;
  skills: Skill[];
  settings: UserSettings;
  onSave: (patch: Partial<UserSettings>) => Promise<void>;
};

export default function GlobalInstructionEditor({ value, defaultSkillId, skills, settings, onSave }: Props): JSX.Element {
  const [instruction, setInstruction] = useState(value);
  const [skillId, setSkillId] = useState(defaultSkillId);
  const [aiName, setAiName] = useState(settings.ai_name);
  const [ownerName, setOwnerName] = useState(settings.owner_name);
  const [saving, setSaving] = useState(false);

  const assistantSkillId = useMemo(
    () => skills.find((skill) => skill.name === '通用助手')?.id ?? '',
    [skills],
  );

  useEffect(() => {
    setInstruction(value);
  }, [value]);

  useEffect(() => {
    setAiName(settings.ai_name);
  }, [settings.ai_name]);

  useEffect(() => {
    setOwnerName(settings.owner_name);
  }, [settings.owner_name]);

  useEffect(() => {
    setSkillId(defaultSkillId || assistantSkillId);
  }, [defaultSkillId, assistantSkillId]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave({
        global_instruction: instruction,
        default_skill_id: skillId,
        ai_name: aiName,
        owner_name: ownerName,
      });
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
              追加到每次对话的 System Prompt 末尾，对所有 Skill 生效
            </Typography.Text>
            <RagMarkdownEditor value={instruction} onChange={setInstruction} height={160} />
          </Flex>
        </Form.Item>
      </Form>
    </Card>
  );
}
