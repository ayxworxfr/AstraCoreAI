import { useEffect, useState } from 'react';
import { Form, Input, InputNumber, Modal, Radio, Select } from 'antd';
import type { CreateTaskRequest, ScheduledTask, TriggerType, UpdateTaskRequest } from '@/features/scheduling/types';

const TIMEZONE_OPTIONS = [
  { value: 'Asia/Shanghai', label: 'Asia/Shanghai (UTC+8)' },
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: 'America/New_York (UTC-5/-4)' },
  { value: 'Europe/London', label: 'Europe/London (UTC+0/+1)' },
  { value: 'Asia/Tokyo', label: 'Asia/Tokyo (UTC+9)' },
];

type FormValues = {
  name?: string;
  prompt: string;
  trigger_type: TriggerType;
  cron_expr?: string;
  interval_seconds?: number;
  run_at?: string;
  timezone: string;
  use_tools: boolean;
};

type Props = {
  open: boolean;
  task: ScheduledTask | null;
  onClose: () => void;
  onCreate: (req: CreateTaskRequest) => Promise<void>;
  onUpdate: (id: string, req: UpdateTaskRequest) => Promise<void>;
};

export default function CreateTaskModal({ open, task, onClose, onCreate, onUpdate }: Props): JSX.Element {
  const [form] = Form.useForm<FormValues>();
  const [triggerType, setTriggerType] = useState<TriggerType>('cron');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (task) {
      const tt = task.trigger_type;
      setTriggerType(tt);
      form.setFieldsValue({
        name: task.name,
        prompt: task.prompt,
        trigger_type: tt,
        cron_expr: tt === 'cron' ? task.trigger_config.expr : undefined,
        interval_seconds: tt === 'interval' ? task.trigger_config.seconds : undefined,
        run_at: tt === 'date' ? task.trigger_config.run_at : undefined,
        timezone: task.timezone,
        use_tools: task.use_tools,
      });
    } else {
      setTriggerType('cron');
      form.resetFields();
      form.setFieldsValue({ trigger_type: 'cron', timezone: 'Asia/Shanghai', use_tools: true });
    }
  }, [open, task, form]);

  const handleOk = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      const triggerConfig =
        values.trigger_type === 'cron'
          ? { expr: values.cron_expr }
          : values.trigger_type === 'interval'
            ? { seconds: values.interval_seconds }
            : { run_at: values.run_at };

      if (task) {
        await onUpdate(task.id, {
          name: values.name,
          prompt: values.prompt,
          trigger_type: values.trigger_type,
          trigger_config: triggerConfig,
          timezone: values.timezone,
          use_tools: values.use_tools,
        });
      } else {
        await onCreate({
          name: values.name,
          prompt: values.prompt,
          trigger_type: values.trigger_type,
          trigger_config: triggerConfig,
          timezone: values.timezone,
          use_tools: values.use_tools,
        });
      }
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={task ? '编辑计划任务' : '新建计划任务'}
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      okText="保存"
      cancelText="取消"
      confirmLoading={submitting}
      width={560}
      destroyOnClose
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item name="name" label="任务名称">
          <Input placeholder="可选，不填则自动生成" maxLength={128} />
        </Form.Item>

        <Form.Item name="prompt" label="执行提示词" rules={[{ required: true, message: '请输入提示词' }]}>
          <Input.TextArea
            placeholder="每次触发时 AI 将执行的指令"
            autoSize={{ minRows: 3, maxRows: 6 }}
            maxLength={4000}
            showCount
          />
        </Form.Item>

        <Form.Item name="trigger_type" label="触发方式" rules={[{ required: true }]}>
          <Radio.Group
            onChange={(e) => {
              setTriggerType(e.target.value as TriggerType);
              form.resetFields(['cron_expr', 'interval_seconds', 'run_at']);
            }}
          >
            <Radio.Button value="cron">Cron 表达式</Radio.Button>
            <Radio.Button value="interval">固定间隔</Radio.Button>
            <Radio.Button value="date">单次执行</Radio.Button>
          </Radio.Group>
        </Form.Item>

        {triggerType === 'cron' && (
          <Form.Item
            name="cron_expr"
            label="Cron 表达式"
            rules={[{ required: true, message: '请输入 cron 表达式' }]}
            extra="示例：0 9 * * 1-5 (工作日 9:00)"
          >
            <Input placeholder="0 9 * * *" maxLength={128} />
          </Form.Item>
        )}

        {triggerType === 'interval' && (
          <Form.Item
            name="interval_seconds"
            label="间隔（秒）"
            rules={[{ required: true, message: '请输入间隔秒数' }, { type: 'number', min: 60, message: '最小 60 秒' }]}
          >
            <InputNumber min={60} style={{ width: '100%' }} placeholder="3600" />
          </Form.Item>
        )}

        {triggerType === 'date' && (
          <Form.Item
            name="run_at"
            label="执行时间（ISO8601）"
            rules={[{ required: true, message: '请输入执行时间' }]}
            extra="示例：2026-07-01T09:00:00+08:00"
          >
            <Input placeholder="2026-07-01T09:00:00+08:00" />
          </Form.Item>
        )}

        <Form.Item name="timezone" label="时区">
          <Select options={TIMEZONE_OPTIONS} showSearch />
        </Form.Item>

        <Form.Item name="use_tools" label="启用工具调用">
          <Radio.Group>
            <Radio value={true}>是</Radio>
            <Radio value={false}>否</Radio>
          </Radio.Group>
        </Form.Item>
      </Form>
    </Modal>
  );
}
