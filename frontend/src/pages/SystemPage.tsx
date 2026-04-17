import { useEffect, useState, useCallback } from 'react';
import { Flex, Typography, Button, Switch, Tooltip } from 'antd';
import { ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import HealthStatusCard, { type CheckResult } from '../components/system/HealthStatusCard';
import { getHealth, getReady } from '../services/healthService';
import { normalizeError } from '../services/apiClient';

export default function SystemPage(): JSX.Element {
  const [health, setHealth] = useState<CheckResult>({ status: 'loading', message: '检查中...' });
  const [ready, setReady] = useState<CheckResult>({ status: 'loading', message: '检查中...' });
  const [autoRefresh, setAutoRefresh] = useState(false);

  const check = useCallback(async () => {
    setHealth({ status: 'loading', message: '检查中...' });
    setReady({ status: 'loading', message: '检查中...' });

    await Promise.allSettled([
      getHealth()
        .then((h) => setHealth({ status: 'ok', message: h.status }))
        .catch((e: unknown) => setHealth({ status: 'error', message: normalizeError(e) })),
      getReady()
        .then((r) =>
          setReady({ status: r.status === 'ready' ? 'ok' : 'error', message: r.status === 'ready' ? '就绪' : r.status }),
        )
        .catch((e: unknown) => setReady({ status: 'error', message: normalizeError(e) })),
    ]);
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      void check();
    }, 10000);
    return () => clearInterval(id);
  }, [autoRefresh, check]);

  return (
    <Flex vertical style={{ height: '100%', overflow: 'auto', padding: 24 }} gap={16}>
      <Flex align="center" justify="space-between">
        <Typography.Title level={4} style={{ margin: 0 }}>
          系统状态
        </Typography.Title>
        <Flex gap={8} align="center">
          <Tooltip title={autoRefresh ? '已开启，每 10s 自动刷新' : '开启后每 10s 自动刷新'}>
            <Flex
              align="center"
              gap={6}
              style={{
                padding: '5px 12px',
                borderRadius: 6,
                border: '1px solid rgba(5, 5, 5, 0.15)',
                cursor: 'pointer',
              }}
              onClick={() => setAutoRefresh(!autoRefresh)}
            >
              <SyncOutlined
                spin={autoRefresh}
                style={{ fontSize: 13, color: autoRefresh ? '#1677ff' : 'rgba(0,0,0,0.45)' }}
              />
              <Typography.Text style={{ fontSize: 13, userSelect: 'none' }}>
                自动刷新
              </Typography.Text>
              <Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} />
            </Flex>
          </Tooltip>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              void check();
            }}
          >
            刷新
          </Button>
        </Flex>
      </Flex>
      <Flex gap={12} wrap="wrap">
        <HealthStatusCard title="Health" result={health} />
        <HealthStatusCard title="Ready" result={ready} />
      </Flex>
    </Flex>
  );
}
