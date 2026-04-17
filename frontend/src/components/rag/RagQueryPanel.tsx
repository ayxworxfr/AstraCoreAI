import { Form, Input, InputNumber, Button, Card } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { ragRetrieve } from '../../services/ragService';
import { normalizeError } from '../../services/apiClient';
import type { RagResult } from '../../types/api';

type FormValues = {
  query: string;
  top_k: number;
};

type Props = {
  onResults: (results: RagResult[]) => void;
  loading: boolean;
  onLoadingChange: (loading: boolean) => void;
  onError: (msg: string | null) => void;
};

export default function RagQueryPanel({ onResults, loading, onLoadingChange, onError }: Props): JSX.Element {
  const [form] = Form.useForm<FormValues>();

  const handleFinish = async (values: FormValues) => {
    onLoadingChange(true);
    onError(null);
    try {
      const res = await ragRetrieve({ query: values.query, top_k: values.top_k });
      onResults(res.chunks);
    } catch (e) {
      onError(normalizeError(e));
      onResults([]);
    } finally {
      onLoadingChange(false);
    }
  };

  return (
    <Card title="检索参数">
      <Form
        form={form}
        layout="inline"
        initialValues={{ top_k: 5 }}
        onFinish={(values) => { void handleFinish(values); }}
        style={{ flexWrap: 'wrap', gap: 8 }}
      >
        <Form.Item
          name="query"
          rules={[{ required: true, message: '请输入查询内容' }]}
          style={{ flex: 1, minWidth: 200, marginBottom: 0 }}
        >
          <Input placeholder="输入查询内容" allowClear />
        </Form.Item>
        <Form.Item name="top_k" label="top_k" style={{ marginBottom: 0 }}>
          <InputNumber min={1} max={20} style={{ width: 80 }} />
        </Form.Item>
        <Form.Item style={{ marginBottom: 0 }}>
          <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={loading}>
            检索
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}
