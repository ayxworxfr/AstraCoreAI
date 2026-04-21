import { useEffect } from 'react';
import { Alert, Form, Input, Modal } from 'antd';
import RagMarkdownEditor from '../rag/RagMarkdownEditor';
import type { CreateSkillRequest, Skill } from '../../types/skill';

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
          ? { name: skill.name, description: skill.description, system_prompt: skill.system_prompt }
          : { name: '', description: '', system_prompt: '' },
      );
    }
  }, [open, skill, form]);

  const handleOk = async () => {
    const values = await form.validateFields();
    await onSave(values);
    onClose();
  };

  const title = readOnly ? '查看 Skill' : skill ? '编辑 Skill' : '新建 Skill';

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
      styles={{ body: { maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' } }}
      destroyOnClose
    >
      {readOnly && (
        <Alert message="内置 Skill 不可修改" type="info" showIcon style={{ marginBottom: 16 }} />
      )}
      <Form form={form} layout="vertical" disabled={readOnly}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="代码助手" maxLength={128} />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input placeholder="简短说明这个 Skill 的用途" maxLength={200} />
        </Form.Item>
        <Form.Item
          name="system_prompt"
          label="System Prompt"
          rules={[{ required: true, message: '请输入 System Prompt' }]}
        >
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue, setFieldValue }) => (
              <RagMarkdownEditor
                value={getFieldValue('system_prompt') ?? ''}
                onChange={(v) => setFieldValue('system_prompt', v)}
                height={460}
              />
            )}
          </Form.Item>
        </Form.Item>
      </Form>
    </Modal>
  );
}
