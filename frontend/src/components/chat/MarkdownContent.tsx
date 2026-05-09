import MDEditor from '@uiw/react-md-editor';
import '@uiw/react-markdown-preview/markdown.css';
import { useSettingsStore } from '../../stores/settingsStore';

type Props = { content: string };

export default function MarkdownContent({ content }: Props): JSX.Element {
  const theme = useSettingsStore((s) => s.theme);
  return (
    <div className="md-transparent" data-color-mode={theme}>
      <MDEditor.Markdown source={content} />
    </div>
  );
}
