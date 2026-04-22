import { BookOutlined } from '@ant-design/icons';
import { Button, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import { useEffect } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useSkillStore } from '../../stores/skillStore';

export default function SkillSelector({ disabled }: { disabled: boolean }): JSX.Element {
  const { skills, settings, fetchSkills, fetchSettings } = useSkillStore();
  const { activeSkillId, setActiveSkillId } = useChatStore();

  useEffect(() => {
    void fetchSkills();
    void fetchSettings();
  }, [fetchSkills, fetchSettings]);

  const defaultSkill = skills.find((s) => s.id === settings.default_skill_id);
  const defaultLabel = defaultSkill ? `使用默认（${defaultSkill.name}）` : '使用默认（未设置）';

  const items: MenuProps['items'] = [
    {
      key: 'null',
      label: defaultLabel,
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
      ? (defaultSkill?.name ?? '未设置 Skill')
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
