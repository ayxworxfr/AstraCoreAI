import MDEditor from '@uiw/react-md-editor';
import { Card, Tag, Empty, Typography, Flex } from 'antd';
import { useSettingsStore } from '../../stores/settingsStore';
import type { RagResult } from '../../types/api';

type Props = { results: RagResult[] };

function scoreColor(score: number): string {
  if (score >= 0.8) return 'green';
  if (score >= 0.5) return 'orange';
  return 'default';
}

export default function RagResultList({ results }: Props): JSX.Element {
  const theme = useSettingsStore((s) => s.theme);

  if (results.length === 0) {
    return <Empty description="暂无检索结果" style={{ padding: '40px 0' }} />;
  }

  return (
    <Flex vertical gap={12}>
      {results.map((r, i) => (
        <Card
          key={i}
          size="small"
          title={`结果 ${i + 1}`}
          extra={
            <Tag color={scoreColor(r.score)}>
              相关度 {(r.score * 100).toFixed(1)}%
            </Tag>
          }
        >
          <div className="md-transparent" data-color-mode={theme}>
            <MDEditor.Markdown source={r.content} />
          </div>
          {r.citation && (
            <Typography.Text
              type="secondary"
              style={{ fontSize: 12, marginTop: 8, display: 'block' }}
            >
              来源：{r.citation.title ?? r.citation.source_id}
            </Typography.Text>
          )}
        </Card>
      ))}
    </Flex>
  );
}
