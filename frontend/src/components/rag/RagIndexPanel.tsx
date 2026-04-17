import { useState } from 'react';
import { Form, Input, Button, Flex, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { ragIndex } from '../../services/ragService';
import { normalizeError } from '../../services/apiClient';
import RagMarkdownEditor from './RagMarkdownEditor';

type FormValues = {
  document_id: string;
  title?: string;
};

export default function RagIndexPanel(): JSX.Element {
  const [form] = Form.useForm<FormValues>();
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);

  const handleFinish = async (values: FormValues) => {
    if (!content.trim()) {
      void message.error('请输入文档内容');
      return;
    }
    setLoading(true);
    try {
      const res = await ragIndex({
        document_id: values.document_id,
        text: content,
        metadata: values.title ? { title: values.title, source: 'manual' } : { source: 'manual' },
      });
      if (res.success) {
        void message.success(`文档 "${values.document_id}" 索引成功`);
        form.resetFields();
        setContent('');
      } else {
        void message.error(res.message);
      }
    } catch (e) {
      void message.error(normalizeError(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Flex vertical gap={12}>
      <Form
        form={form}
        layout="inline"
        onFinish={(values) => { void handleFinish(values); }}
        style={{ flexShrink: 0 }}
      >
        <Form.Item
          name="document_id"
          rules={[
            { required: true, message: '请输入文档 ID' },
            { pattern: /^[\w-]+$/, message: '只允许字母、数字、下划线和连字符' },
          ]}
          style={{ width: 220, marginBottom: 0 }}
        >
          <Input placeholder="文档 ID，如 my-doc-001" allowClear />
        </Form.Item>
        <Form.Item
          name="title"
          style={{ flex: 1, maxWidth: 400, marginBottom: 0 }}
        >
          <Input placeholder="标题（可选）" allowClear />
        </Form.Item>
        <Form.Item style={{ marginBottom: 0 }}>
          <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={loading}>
            写入索引
          </Button>
        </Form.Item>
      </Form>
      <RagMarkdownEditor value={content} onChange={setContent} />
    </Flex>
  );
}
