import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Flex, Input, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useSkillStore } from '@/features/skills/store/skillStore';
import SkillCard from '@/features/skills/components/SkillCard';
import SkillModal from '@/features/skills/components/SkillModal';
import type { CreateSkillRequest, Skill } from '@/features/skills/types';
import GlobalInstructionEditor from '@/features/skills/components/GlobalInstructionEditor';
import AppScrollArea from '@/shared/components/AppScrollArea';

const CATEGORY_ORDER = ['general', 'coding', 'writing', 'analysis', 'finance', 'language', 'ops', 'entertainment'];

export default function SkillsPage(): JSX.Element {
  const { skills, settings, isLoading, error, fetchSkills, fetchSettings, createSkill, updateSkill, deleteSkill, saveSettings, clearError } =
    useSkillStore();

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
    (s) =>
      !search ||
      s.name.includes(search) ||
      (s.display_name && s.display_name.includes(search)) ||
      s.description?.includes(search),
  );

  const grouped = useMemo(() => {
    const map = new Map<string, Skill[]>();
    for (const skill of filtered) {
      const cat = skill.category ?? 'general';
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(skill);
    }
    const ordered = [...map.entries()].sort(([a], [b]) => {
      const ia = CATEGORY_ORDER.indexOf(a);
      const ib = CATEGORY_ORDER.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });
    return ordered;
  }, [filtered]);

  const CATEGORY_LABELS: Record<string, string> = {
    general: '通用',
    coding: '编程',
    writing: '写作',
    analysis: '分析',
    finance: '金融',
    language: '语言',
    ops: '运维',
    entertainment: '娱乐',
  };

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <Flex vertical style={{ padding: 24 }} gap={16}>
        <Flex align="center" justify="space-between">
          <Typography.Title level={4} style={{ margin: 0 }}>
            技能管理
          </Typography.Title>
          <Button icon={<PlusOutlined />} type="primary" onClick={handleCreate}>
            新建技能
          </Button>
        </Flex>

        {error && (
          <Alert type="error" message={error} closable onClose={clearError} />
        )}

        <GlobalInstructionEditor
          value={settings.global_instruction}
          settings={settings}
          onSave={saveSettings}
        />

        <Input.Search
          placeholder="搜索技能名称或描述"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
          style={{ maxWidth: 320 }}
        />

        {grouped.map(([category, catSkills]) => (
          <Flex key={category} vertical gap={8}>
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 500 }}>
              {CATEGORY_LABELS[category] ?? category}
            </Typography.Text>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                gap: 12,
                alignItems: 'start',
              }}
            >
              {catSkills.map((skill) => (
                <SkillCard
                  key={skill.id}
                  skill={skill}
                  onEdit={handleEdit}
                  onDelete={handleDelete}
                  onView={handleView}
                />
              ))}
            </div>
          </Flex>
        ))}

        {!isLoading && filtered.length === 0 && (
          <Typography.Text type="secondary">
            {search ? '没有匹配的技能' : '暂无技能，点击右上角新建'}
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
    </AppScrollArea>
  );
}
