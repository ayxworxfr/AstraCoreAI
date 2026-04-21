import { BookOutlined } from '@ant-design/icons';
import { Button, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import { useEffect } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useSkillStore } from '../../stores/skillStore';

export default function SkillSelector({ disabled }: { disabled: boolean }): JSX.Element {
  const { skills, fetchSkills } = useSkillStore();
  const { activeSkillId, setActiveSkillId } = useChatStore();

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const items: MenuProps['items'] = [
    {
      key: 'null',
      label: '使用默认',
    },
    { type: 'divider' },
    ...skills.map((s) => ({
      key: s.id,
      label: s.name,
    })),
    { type: 'divider' as const },
    {
      key: 'none',
      label: '无（不使用 Skill）',
      danger: true,
    },
  ];

  const activeSkill = skills.find((s) => s.id === activeSkillId);
  const label =
    activeSkillId === null
      ? '默认 Skill'
      : activeSkillId === 'none'
        ? '无 Skill'
        : (activeSkill?.name ?? '选择 Skill');

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    setActiveSkillId(key === 'null' ? null : key);
  };

  return (
    <Dropdown menu={{ items, onClick: handleMenuClick, selectedKeys: [activeSkillId ?? 'null'] }} disabled={disabled}>
      <Button size="small" icon={<BookOutlined />} type={activeSkillId === 'none' ? 'default' : 'text'}>
        {label}
      </Button>
    </Dropdown>
  );
}
