import { useState } from 'react';
import { Button, Card, Flex, Form, Input, Typography, theme as antTheme } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/features/auth/store/authStore';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { normalizeError } from '@/shared/services/apiClient';

type LoginForm = { username: string; password: string };

export default function LoginPage(): JSX.Element {
  const { token } = antTheme.useToken();
  const appTheme = useSettingsStore((s) => s.theme);
  const login = useAuthStore((s) => s.login);
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const isDark = appTheme === 'dark';
  const inputStyle = isDark
    ? {
        background: 'rgba(15, 23, 42, 0.72)',
        borderColor: 'rgba(96, 165, 250, 0.24)',
        color: token.colorText,
      }
    : undefined;

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
    <Flex
      align="center"
      justify="center"
      style={{
        minHeight: '100vh',
        padding: 24,
        background: isDark
          ? 'radial-gradient(circle at 30% 20%, rgba(37, 99, 235, 0.24), transparent 34%), radial-gradient(circle at 72% 78%, rgba(20, 184, 166, 0.16), transparent 30%), #0d1117'
          : 'radial-gradient(circle at 30% 20%, rgba(22, 119, 255, 0.12), transparent 34%), #f5f7fa',
      }}
    >
      <Card
        style={{
          width: 400,
          borderRadius: 18,
          border: isDark ? '1px solid rgba(148, 163, 184, 0.18)' : `1px solid ${token.colorBorderSecondary}`,
          background: isDark ? 'rgba(17, 24, 39, 0.86)' : token.colorBgContainer,
          boxShadow: isDark
            ? '0 24px 80px rgba(0, 0, 0, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.05)'
            : '0 16px 48px rgba(15, 23, 42, 0.10)',
          backdropFilter: 'blur(18px)',
        }}
        styles={{ body: { padding: 30 } }}
      >
        <Flex vertical gap={24}>
          <div>
            <Typography.Title
              level={3}
              style={{
                margin: 0,
                letterSpacing: '-0.03em',
                color: isDark ? '#f8fafc' : token.colorText,
              }}
            >
              AstraCore AI
            </Typography.Title>
            <Typography.Text style={{ color: isDark ? 'rgba(203, 213, 225, 0.68)' : token.colorTextSecondary }}>
              登录以继续
            </Typography.Text>
          </div>

          <Form<LoginForm>
            layout="vertical"
            autoComplete="off"
            onFinish={(v) => { void handleSubmit(v); }}
          >
            <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
              <Input autoComplete="off" size="large" style={inputStyle} />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true }]}>
              <Input.Password autoComplete="new-password" size="large" style={inputStyle} />
            </Form.Item>

            {error && (
              <Typography.Text type="danger" style={{ display: 'block', marginBottom: 12 }}>
                {error}
              </Typography.Text>
            )}

            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" loading={loading} block size="large">
                登录
              </Button>
            </Form.Item>
          </Form>
        </Flex>
      </Card>
    </Flex>
  );
}
