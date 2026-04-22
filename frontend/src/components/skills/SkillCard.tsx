import { DeleteOutlined, EditOutlined, LockOutlined } from '@ant-design/icons';
import { Button, Card, Flex, Popconfirm, Tag, Tooltip, Typography, theme } from 'antd';
import type { Skill } from '../../types/skill';

type Props = {
  skill: Skill;
  isActive?: boolean;
  onEdit: (skill: Skill) => void;
  onDelete: (id: string) => void;
  onView: (skill: Skill) => void;
};

export default function SkillCard({ skill, isActive, onEdit, onDelete, onView }: Props): JSX.Element {
  const { token } = theme.useToken();

  return (
    <Card
      hoverable
      style={{
        borderColor: isActive ? token.colorPrimary : undefined,
        boxShadow: isActive ? `0 0 0 2px ${token.colorPrimaryBg}` : undefined,
      }}
      styles={{
        body: {
          display: 'flex',
          flexDirection: 'column',
          padding: '16px',
          gap: 0,
        },
      }}
    >
      {/* 标题行 */}
      <Flex align="center" justify="space-between" gap={8} style={{ marginBottom: 8 }}>
        <Typography.Text strong ellipsis={{ tooltip: skill.name }} style={{ flex: 1, fontSize: 14 }}>
          {skill.name}
        </Typography.Text>
        <Flex gap={4} style={{ flexShrink: 0 }}>
          {skill.is_builtin && (
            <Tag color="default" style={{ margin: 0, fontSize: 11 }}>
              内置
            </Tag>
          )}
          {isActive && (
            <Tag color="processing" style={{ margin: 0, fontSize: 11 }}>
              使用中
            </Tag>
          )}
        </Flex>
      </Flex>

      {/* 描述 */}
      <Typography.Text
        type="secondary"
        ellipsis={{ tooltip: skill.description || '暂无描述' }}
        style={{ fontSize: 12, lineHeight: 1.65, marginBottom: 0, display: 'block' }}
      >
        {skill.description || '暂无描述'}
      </Typography.Text>

      {/* 底部操作栏 */}
      <Flex
        align="center"
        justify="center"
        gap={8}
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        {skill.is_builtin ? (
          <Tooltip title="查看内置 Skill">
            <Button
              type="text"
              size="small"
              icon={<LockOutlined />}
              onClick={() => onView(skill)}
              style={{ color: token.colorTextTertiary }}
            />
          </Tooltip>
        ) : (
          <>
            <Tooltip title="编辑">
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                onClick={() => onEdit(skill)}
              />
            </Tooltip>
            <Popconfirm
              title="确认删除此 Skill？"
              onConfirm={() => onDelete(skill.id)}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Tooltip title="删除">
                <Button type="text" size="small" icon={<DeleteOutlined />} danger />
              </Tooltip>
            </Popconfirm>
          </>
        )}
      </Flex>
    </Card>
  );
}
