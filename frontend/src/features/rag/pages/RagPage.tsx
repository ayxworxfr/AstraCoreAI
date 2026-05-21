import { useState } from 'react';
import { Flex, Typography, Alert, Tabs } from 'antd';
import { SearchOutlined, FileAddOutlined } from '@ant-design/icons';
import RagQueryPanel from '@/features/rag/components/RagQueryPanel';
import RagResultList from '@/features/rag/components/RagResultList';
import RagIndexPanel from '@/features/rag/components/RagIndexPanel';
import type { RagResult } from '@/shared/types/api';
import AppScrollArea from '@/shared/components/AppScrollArea';

export default function RagPage(): JSX.Element {
  const [results, setResults] = useState<RagResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <Flex vertical style={{ padding: 24 }} gap={16}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        RAG 知识库
      </Typography.Title>
      <Tabs
        defaultActiveKey="search"
        items={[
          {
            key: 'search',
            label: (
              <span>
                <SearchOutlined />
                检索文档
              </span>
            ),
            children: (
              <Flex vertical gap={12}>
                <RagQueryPanel
                  onResults={setResults}
                  loading={loading}
                  onLoadingChange={setLoading}
                  onError={setError}
                />
                {error && (
                  <Alert type="error" message={error} closable onClose={() => setError(null)} />
                )}
                <RagResultList results={results} />
              </Flex>
            ),
          },
          {
            key: 'index',
            label: (
              <span>
                <FileAddOutlined />
                写入文档
              </span>
            ),
            children: <RagIndexPanel />,
          },
        ]}
      />
      </Flex>
    </AppScrollArea>
  );
}
