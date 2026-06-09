import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Flex, Input, Select, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useSkillStore } from '@/features/skills/store/skillStore';
import SkillCard from '@/features/skills/components/SkillCard';
import SkillModal from '@/features/skills/components/SkillModal';
import type { CreateSkillRequest, Skill } from '@/features/skills/types';
import GlobalInstructionEditor from '@/features/skills/components/GlobalInstructionEditor';
import AppScrollArea from '@/shared/components/AppScrollArea';

const CATEGORY_ORDER = ['general', 'coding', 'writing', 'analysis', 'finance', 'language', 'ops', 'entertainment'];

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

export default function SkillsPage(): JSX.Element {
  const { skills, settings, isLoading, error, fetchSkills, fetchSettings, createSkill, updateSkill, deleteSkill, saveSettings, clearError } =
    useSkillStore();

  const [modalOpen, setModalOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState<string>('all');

  useEffect(() => {
    void fetchSkills();
    void fetchSettings();
  }, [fetchSkills, fetchSettings]);

  const handleCreate = () => { setEditingSkill(null); setReadOnly(false); setModalOpen(true); };
  const handleEdit = (skill: Skill) => { setEditingSkill(skill); setReadOnly(false); setModalOpen(true); };
  const handleView = (skill: Skill) => { setEditingSkill(skill); setReadOnly(true); setModalOpen(true); };
  const handleDelete = async (id: string) => { await deleteSkill(id); };
  const handleSave = async (req: CreateSkillRequest) => {
    if (editingSkill) await updateSkill(editingSkill.id, req);
    else await createSkill(req);
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

  const filtered = useMemo(() =>
    sortedSkills.filter((s) =>
      !search ||
      s.name.includes(search) ||
      (s.display_name && s.display_name.includes(search)) ||
      s.description?.includes(search),
    ),
  [sortedSkills, search]);

  // 当前过滤结果中存在的分类（按预设顺序）
  const availableCategories = useMemo(() => {
    const cats = new Set(filtered.map((s) => s.category ?? 'general'));
    return CATEGORY_ORDER.filter((c) => cats.has(c));
  }, [filtered]);

  // 分类切换后若该分类已无结果则重置
  useEffect(() => {
    if (activeCategory !== 'all' && !availableCategories.includes(activeCategory)) {
      setActiveCategory('all');
    }
  }, [activeCategory, availableCategories]);

  const categoryOptions = useMemo(() => [
    { label: `全部 (${filtered.length})`, value: 'all' },
    ...availableCategories.map((cat) => ({
      label: CATEGORY_LABELS[cat] ?? cat,
      value: cat,
    })),
  ], [filtered.length, availableCategories]);

  const displaySkills = useMemo(() => {
    if (activeCategory === 'all') return filtered;
    return filtered.filter((s) => (s.category ?? 'general') === activeCategory);
  }, [filtered, activeCategory]);

  return (
    <AppScrollArea style={{ height: '100%' }}>
      <Flex vertical style={{ padding: 24 }} gap={16}>
        {/* 页头 */}
        <Flex align="center" justify="space-between">
          <Typography.Title level={4} style={{ margin: 0 }}>技能管理</Typography.Title>
          <Button icon={<PlusOutlined />} type="primary" onClick={handleCreate}>新建技能</Button>
        </Flex>

        {error && <Alert type="error" message={error} closable onClose={clearError} />}

        <GlobalInstructionEditor
          value={settings.global_instruction}
          settings={settings}
          onSave={saveSettings}
        />

        {/* 搜索 + 分类筛选 */}
        <Flex align="center" gap={12} wrap="wrap">
          <Select
            options={categoryOptions}
            value={activeCategory}
            onChange={setActiveCategory}
            showSearch
            filterOption={(input, opt) =>
              (opt?.label as string ?? '').toLowerCase().includes(input.toLowerCase())
            }
            style={{ width: 160 }}
          />
          <Input.Search
            placeholder="搜索技能名称或描述"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
            style={{ width: 240 }}
          />
        </Flex>

        {/* 技能网格 */}
        {!isLoading && displaySkills.length === 0 ? (
          <Typography.Text type="secondary">
            {search ? `未找到"${search}"相关技能` : '暂无技能，点击右上角新建'}
          </Typography.Text>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
              gap: 12,
              alignItems: 'start',
            }}
          >
            {displaySkills.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onView={handleView}
              />
            ))}
          </div>
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
