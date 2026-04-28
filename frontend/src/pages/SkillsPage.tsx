import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Flex, Input, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useSkillStore } from '../stores/skillStore';
import { useChatStore } from '../stores/chatStore';
import SkillCard from '../components/skills/SkillCard';
import SkillModal from '../components/skills/SkillModal';
import type { CreateSkillRequest, Skill } from '../types/skill';
import GlobalInstructionEditor from '../components/skills/GlobalInstructionEditor';

export default function SkillsPage(): JSX.Element {
  const { skills, settings, isLoading, error, fetchSkills, fetchSettings, createSkill, updateSkill, deleteSkill, saveSettings, clearError } =
    useSkillStore();
  const { activeSkillId, setActiveSkillId } = useChatStore();

  const [modalOpen, setModalOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [search, setSearch] = useState('');

  useEffect(() => {
    void fetchSkills();
    void fetchSettings();
  }, [fetchSkills, fetchSettings]);

  const handleCreate = () => {
    setEditingSkill(null);
    setReadOnly(false);
    setModalOpen(true);
  };

  const handleEdit = (skill: Skill) => {
    setEditingSkill(skill);
    setReadOnly(false);
    setModalOpen(true);
  };

  const handleView = (skill: Skill) => {
    setEditingSkill(skill);
    setReadOnly(true);
    setModalOpen(true);
  };

  const handleDelete = async (id: string) => {
    await deleteSkill(id);
    if (activeSkillId === id) setActiveSkillId(null);
  };

  const handleSave = async (req: CreateSkillRequest) => {
    if (editingSkill) {
      await updateSkill(editingSkill.id, req);
    } else {
      await createSkill(req);
    }
  };

  const sortedSkills = useMemo(() => {
    const copied = [...skills];
    copied.sort((a, b) => {
      if (a.is_builtin !== b.is_builtin) return a.is_builtin ? -1 : 1;
      if (a.order !== b.order) return a.order - b.order;
      return a.created_at.localeCompare(b.created_at);
    });
    return copied;
  }, [skills]);

  const filtered = sortedSkills.filter(
    (s) => !search || s.name.includes(search) || s.description?.includes(search),
  );

  return (
    <Flex vertical style={{ height: '100%', overflow: 'auto', padding: 24 }} gap={16}>
      <Flex align="center" justify="space-between">
        <Typography.Title level={4} style={{ margin: 0 }}>
          Skill 管理
        </Typography.Title>
        <Button icon={<PlusOutlined />} type="primary" onClick={handleCreate}>
          新建 Skill
        </Button>
      </Flex>

      {error && (
        <Alert type="error" message={error} closable onClose={clearError} />
      )}

      <GlobalInstructionEditor
        value={settings.global_instruction}
        defaultSkillId={settings.default_skill_id}
        skills={sortedSkills}
        settings={settings}
        onSave={saveSettings}
      />

      <Input.Search
        placeholder="搜索 Skill 名称或描述"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        allowClear
        style={{ maxWidth: 320 }}
      />

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 12,
          alignItems: 'start',
        }}
      >
        {filtered.map((skill) => (
          <SkillCard
            key={skill.id}
            skill={skill}
            isActive={activeSkillId === skill.id}
            onEdit={handleEdit}
            onDelete={handleDelete}
            onView={handleView}
          />
        ))}
      </div>
      {!isLoading && filtered.length === 0 && (
        <Typography.Text type="secondary">
          {search ? '没有匹配的 Skill' : '暂无 Skill，点击右上角新建'}
        </Typography.Text>
      )}

      <SkillModal
        open={modalOpen}
        skill={editingSkill}
        readOnly={readOnly}
        onClose={() => setModalOpen(false)}
        onSave={handleSave}
      />
    </Flex>
  );
}
