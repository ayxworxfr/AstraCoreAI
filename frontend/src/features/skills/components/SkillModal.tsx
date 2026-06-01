import { useEffect } from 'react';
import { Alert, Form, Input, Modal, Select } from 'antd';
import RagMarkdownEditor from '@/features/rag/components/RagMarkdownEditor';
import type { CreateSkillRequest, Skill } from '@/features/skills/types';
import AppScrollArea from '@/shared/components/AppScrollArea';

const CATEGORY_OPTIONS = [
  { value: 'general', label: '通用 (general)' },
  { value: 'coding', label: '编程 (coding)' },
  { value: 'writing', label: '写作 (writing)' },
  { value: 'analysis', label: '分析 (analysis)' },
  { value: 'finance', label: '金融 (finance)' },
  { value: 'language', label: '语言 (language)' },
  { value: 'ops', label: '运维 (ops)' },
];

type Props = {
  open: boolean;
  skill: Skill | null;
  readOnly?: boolean;
  onClose: () => void;
  onSave: (req: CreateSkillRequest) => Promise<void>;
};

export default function SkillModal({ open, skill, readOnly, onClose, onSave }: Props): JSX.Element {
  const [form] = Form.useForm<CreateSkillRequest>();

  useEffect(() => {
    if (open) {
      form.setFieldsValue(
        skill
          ? {
              name: skill.name,
              display_name: skill.display_name,
              description: skill.description,
              instructions: skill.instructions,
              category: skill.category ?? undefined,
            }
          : { name: '', display_name: '', description: '', instructions: '', category: undefined },
      );
    }
  }, [open, skill, form]);

  const handleOk = async () => {
    const values = await form.validateFields();
    await onSave(values);
    onClose();
  };

  const title = readOnly ? '查看技能' : skill ? '编辑技能' : '新建技能';

  return (
    <Modal
      title={title}
      open={open}
      onOk={readOnly ? undefined : handleOk}
      onCancel={onClose}
      okText="保存"
      cancelText={readOnly ? '关闭' : '取消'}
      footer={readOnly ? null : undefined}
      width="min(960px, 90vw)"
      style={{ top: 40 }}
      destroyOnClose
    >
      <AppScrollArea style={{ maxHeight: 'calc(100vh - 160px)' }}>
        <div style={{ paddingRight: 8 }}>
          {readOnly && (
            <Alert message="内置技能不可修改" type="info" showIcon style={{ marginBottom: 16 }} />
          )}
          <Form form={form} layout="vertical" disabled={readOnly}>
            <Form.Item name="name" label="名称（kebab-case）" rules={[{ required: true, message: '请输入名称' }]}>
              <Input placeholder="code-assistant" maxLength={128} />
            </Form.Item>
            <Form.Item name="display_name" label="显示名称">
              <Input placeholder="代码助手（留空则显示名称字段）" maxLength={64} />
            </Form.Item>
            <Form.Item name="category" label="分类">
              <Select options={CATEGORY_OPTIONS} placeholder="选择分类（可选）" allowClear />
            </Form.Item>
            <Form.Item name="description" label="描述">
              <Input placeholder="简短说明这个技能的用途和适用场景" maxLength={200} />
            </Form.Item>
            <Form.Item
              name="instructions"
              label="技能说明"
              rules={[{ required: true, message: '请输入技能说明' }]}
            >
              <Form.Item noStyle shouldUpdate>
                {({ getFieldValue, setFieldValue }) => (
                  <RagMarkdownEditor
                    value={getFieldValue('instructions') ?? ''}
                    onChange={(v) => setFieldValue('instructions', v)}
                    height={460}
                  />
                )}
              </Form.Item>
            </Form.Item>
          </Form>
        </div>
      </AppScrollArea>
    </Modal>
  );
}
