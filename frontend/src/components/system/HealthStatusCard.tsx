import { Card, Badge, Typography } from 'antd';

type Status = 'ok' | 'error' | 'loading';

export type CheckResult = {
  status: Status;
  message: string;
};

type Props = {
  title: string;
  result: CheckResult;
};

const BADGE_STATUS_MAP: Record<Status, 'success' | 'error' | 'processing'> = {
  ok: 'success',
  error: 'error',
  loading: 'processing',
};

export default function HealthStatusCard({ title, result }: Props): JSX.Element {
  return (
    <Card style={{ minWidth: 220, flex: 1 }}>
      <Badge status={BADGE_STATUS_MAP[result.status]} text={title} />
      <Typography.Text
        type={result.status === 'error' ? 'danger' : 'secondary'}
        style={{ display: 'block', marginTop: 8, fontSize: 13 }}
      >
        {result.message}
      </Typography.Text>
    </Card>
  );
}
