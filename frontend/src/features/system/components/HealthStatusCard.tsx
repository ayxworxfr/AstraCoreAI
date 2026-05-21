import { Card, Flex, Typography } from 'antd';
import {
  CheckCircleFilled,
  CloseCircleFilled,
  SyncOutlined,
} from '@ant-design/icons';

type Status = 'ok' | 'error' | 'loading';

export type CheckResult = {
  status: Status;
  message: string;
};

type Props = {
  title: string;
  subtitle?: string;
  result: CheckResult;
};

const STATUS_CONFIG: Record<
  Status,
  { icon: React.ReactNode; color: string; bg: string }
> = {
  ok: {
    icon: <CheckCircleFilled />,
    color: '#52c41a',
    bg: '#f6ffed',
  },
  error: {
    icon: <CloseCircleFilled />,
    color: '#ff4d4f',
    bg: '#fff2f0',
  },
  loading: {
    icon: <SyncOutlined spin />,
    color: '#1677ff',
    bg: '#e6f4ff',
  },
};

export default function HealthStatusCard({ title, subtitle, result }: Props): JSX.Element {
  const cfg = STATUS_CONFIG[result.status];

  return (
    <Card style={{ flex: 1 }} styles={{ body: { padding: '20px 24px' } }}>
      <Flex align="center" gap={16}>
        <div
          style={{
            width: 52,
            height: 52,
            borderRadius: 14,
            background: cfg.bg,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 26,
            color: cfg.color,
            flexShrink: 0,
          }}
        >
          {cfg.icon}
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {title}
          </Typography.Text>
          {subtitle && (
            <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
              {subtitle}
            </Typography.Text>
          )}
          <Typography.Text
            strong
            style={{
              display: 'block',
              fontSize: 20,
              marginTop: 2,
              color: result.status === 'error' ? '#ff4d4f' : undefined,
            }}
          >
            {result.message}
          </Typography.Text>
        </div>
      </Flex>
    </Card>
  );
}
