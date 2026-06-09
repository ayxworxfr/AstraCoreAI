import { useState } from 'react';
import { Button, Card, Flex, Form, Input, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/features/auth/store/authStore';
import { normalizeError } from '@/shared/services/apiClient';

type LoginForm = { username: string; password: string };

export default function LoginPage(): JSX.Element {
  const login = useAuthStore((s) => s.login);
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (values: LoginForm) => {
    setLoading(true);
    setError(null);
    try {
      await login(values.username, values.password);
      navigate('/chat', { replace: true });
    } catch (e) {
      setError(normalizeError(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Flex align="center" justify="center" style={{ height: '100vh', background: '#f5f5f5' }}>
      <Card style={{ width: 360, boxShadow: '0 4px 24px rgba(0,0,0,0.08)' }}>
        <Flex vertical gap={24}>
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>AstraCore AI</Typography.Title>
            <Typography.Text type="secondary">登录以继续</Typography.Text>
          </div>

          <Form<LoginForm>
            layout="vertical"
            initialValues={{ username: 'admin', password: 'admin123' }}
            onFinish={(v) => { void handleSubmit(v); }}
          >
            <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
              <Input autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true }]}>
              <Input.Password autoComplete="current-password" />
            </Form.Item>

            {error && (
              <Typography.Text type="danger" style={{ display: 'block', marginBottom: 12 }}>
                {error}
              </Typography.Text>
            )}

            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" loading={loading} block>
                登录
              </Button>
            </Form.Item>
          </Form>
        </Flex>
      </Card>
    </Flex>
  );
}
