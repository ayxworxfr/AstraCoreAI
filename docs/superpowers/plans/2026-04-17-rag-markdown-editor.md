# RAG Markdown Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 RAG "写入文档" 页面从简单 TextArea 升级为左右分栏 Markdown 编辑器，顶部工具条放字段和提交按钮，编辑器占据主体空间。

**Architecture:** 新建 `RagMarkdownEditor` 封装 `@uiw/react-md-editor`，负责 dark/light 模式同步；`RagIndexPanel` 重写为 Flex 布局，顶部 inline Form 放元数据字段和提交按钮，下方放编辑器；反馈改用 antd 静态 `message` API，不再内嵌 Alert 占用空间。

**Tech Stack:** React 18, Ant Design 5, @uiw/react-md-editor, Zustand (settingsStore for theme)

---

## File Map

| 操作 | 文件 | 职责 |
|------|------|------|
| 安装 | `frontend/package.json` | 添加 `@uiw/react-md-editor` 依赖 |
| 新建 | `frontend/src/components/rag/RagMarkdownEditor.tsx` | 封装 MDEditor，同步 dark/light 模式 |
| 修改 | `frontend/src/components/rag/RagIndexPanel.tsx` | 顶部 Form 条 + 编辑器全高布局，去掉 Card 和内嵌 Alert |

---

### Task 1: 安装 @uiw/react-md-editor

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: 在 frontend 目录安装依赖**

```bash
cd frontend && npm install @uiw/react-md-editor
```

Expected output: 包含 `added ... packages` 的成功信息，`package.json` dependencies 中出现 `"@uiw/react-md-editor": "^x.x.x"`

- [ ] **Step 2: 验证安装**

```bash
ls frontend/node_modules/@uiw/react-md-editor/
```

Expected: 目录存在，包含 `package.json`

- [ ] **Step 3: Commit**

```bash
cd frontend && git add package.json package-lock.json
git commit -m "chore: install @uiw/react-md-editor"
```

---

### Task 2: 新建 RagMarkdownEditor 组件

**Files:**
- Create: `frontend/src/components/rag/RagMarkdownEditor.tsx`

- [ ] **Step 1: 创建组件文件**

创建 `frontend/src/components/rag/RagMarkdownEditor.tsx`，内容如下：

```tsx
import MDEditor from '@uiw/react-md-editor';
import '@uiw/react-md-editor/markdown-editor.css';
import { useSettingsStore } from '../../stores/settingsStore';

type Props = {
  value: string;
  onChange: (value: string) => void;
  height?: number;
};

export default function RagMarkdownEditor({ value, onChange, height = 550 }: Props): JSX.Element {
  const theme = useSettingsStore((s) => s.theme);

  return (
    <div data-color-mode={theme}>
      <MDEditor
        value={value}
        onChange={(v) => onChange(v ?? '')}
        height={height}
        preview="live"
      />
    </div>
  );
}
```

**说明：**
- `data-color-mode` 包裹 div 让 MDEditor 跟随 Ant Design 的 light/dark 主题
- `preview="live"` 是左右分栏实时预览模式
- `height` 默认 550px，父组件可覆盖

- [ ] **Step 2: 验证 TypeScript 无报错**

```bash
cd frontend && npx tsc --noEmit
```

Expected: 无错误输出

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/rag/RagMarkdownEditor.tsx
git commit -m "feat: add RagMarkdownEditor wrapper with dark/light theme sync"
```

---

### Task 3: 重写 RagIndexPanel

**Files:**
- Modify: `frontend/src/components/rag/RagIndexPanel.tsx`

- [ ] **Step 1: 完整替换 RagIndexPanel.tsx**

用以下内容替换 `frontend/src/components/rag/RagIndexPanel.tsx` 的全部内容：

```tsx
import { useState } from 'react';
import { Form, Input, Button, Flex, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { ragIndex } from '../../services/ragService';
import { normalizeError } from '../../services/apiClient';
import RagMarkdownEditor from './RagMarkdownEditor';

type FormValues = {
  document_id: string;
  title: string;
};

export default function RagIndexPanel(): JSX.Element {
  const [form] = Form.useForm<FormValues>();
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);

  const handleFinish = async (values: FormValues) => {
    if (!content.trim()) {
      void message.error('请输入文档内容');
      return;
    }
    setLoading(true);
    try {
      const res = await ragIndex({
        document_id: values.document_id,
        text: content,
        metadata: values.title ? { title: values.title, source: 'manual' } : { source: 'manual' },
      });
      if (res.success) {
        void message.success(`文档 "${values.document_id}" 索引成功`);
        form.resetFields();
        setContent('');
      } else {
        void message.error(res.message);
      }
    } catch (e) {
      void message.error(normalizeError(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Flex vertical gap={12}>
      <Form
        form={form}
        layout="inline"
        onFinish={(values) => { void handleFinish(values); }}
        style={{ flexShrink: 0 }}
      >
        <Form.Item
          name="document_id"
          rules={[
            { required: true, message: '请输入文档 ID' },
            { pattern: /^[\w-]+$/, message: '只允许字母、数字、下划线和连字符' },
          ]}
          style={{ width: 220, marginBottom: 0 }}
        >
          <Input placeholder="文档 ID，如 my-doc-001" allowClear />
        </Form.Item>
        <Form.Item
          name="title"
          style={{ flex: 1, maxWidth: 400, marginBottom: 0 }}
        >
          <Input placeholder="标题（可选）" allowClear />
        </Form.Item>
        <Form.Item style={{ marginBottom: 0 }}>
          <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={loading}>
            写入索引
          </Button>
        </Form.Item>
      </Form>
      <RagMarkdownEditor value={content} onChange={setContent} />
    </Flex>
  );
}
```

**关键改动：**
- 移除 `Card` 包裹和 `Typography.Text` 说明，减少嵌套层级
- `content` 状态独立于 `Form`，编辑器受控
- 反馈从内嵌 `Alert` 改为 `message.success / message.error` 浮层提示
- 布局从 `vertical Form` 改为顶部 `inline Form` + 下方编辑器

- [ ] **Step 2: 验证 TypeScript 无报错**

```bash
cd frontend && npx tsc --noEmit
```

Expected: 无错误输出

- [ ] **Step 3: 启动开发服务器验证视觉效果**

```bash
cd frontend && npm run dev
```

打开浏览器 → 进入 RAG → 点击"写入文档" Tab，确认：
1. 顶部一行展示文档 ID、标题输入框和写入按钮
2. 下方为左右分栏编辑器（左侧 Markdown 编辑 + 工具栏，右侧实时预览）
3. 切换深色/浅色主题，编辑器跟随变化
4. 填写文档 ID 和内容，点击写入索引，成功后字段和编辑器均清空，右上角出现成功提示

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/rag/RagIndexPanel.tsx
git commit -m "feat: redesign RagIndexPanel with markdown editor and header toolbar"
```
