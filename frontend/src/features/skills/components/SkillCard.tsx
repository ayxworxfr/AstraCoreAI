import { DeleteOutlined, EditOutlined, LockOutlined } from '@ant-design/icons';
import { Button, Card, Flex, Popconfirm, Tag, Tooltip, Typography, theme } from 'antd';
import type { Skill } from '@/features/skills/types';

type Props = {
  skill: Skill;
  onEdit: (skill: Skill) => void;
  onDelete: (id: string) => void;
  onView: (skill: Skill) => void;
};

export default function SkillCard({ skill, onEdit, onDelete, onView }: Props): JSX.Element {
  const { token } = theme.useToken();
  const displayName = skill.display_name || skill.name;

  return (
    <Card
      hoverable
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
      <Flex align="center" justify="space-between" gap={8} style={{ marginBottom: 6 }}>
        <Typography.Text strong ellipsis={{ tooltip: displayName }} style={{ flex: 1, fontSize: 14 }}>
          {displayName}
        </Typography.Text>
        <Flex gap={4} style={{ flexShrink: 0 }}>
          {skill.is_builtin && (
            <Tag color="default" style={{ margin: 0, fontSize: 11 }}>
              内置
            </Tag>
          )}
          {skill.category && (
            <Tag color="blue" style={{ margin: 0, fontSize: 11 }}>
              {skill.category}
            </Tag>
          )}
        </Flex>
      </Flex>

      {/* 技能 ID */}
      <Typography.Text type="secondary" style={{ fontSize: 11, marginBottom: 4 }}>
        {skill.name}
      </Typography.Text>

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
          <Tooltip title="查看内置技能">
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
              title="确认删除此技能？"
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
