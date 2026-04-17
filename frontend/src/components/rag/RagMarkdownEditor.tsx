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
