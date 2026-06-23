import { SoundOutlined, StopOutlined, SettingOutlined } from '@ant-design/icons';
import { Button, Flex, Popover, Select, Slider, Tooltip, Typography } from 'antd';
import { useTTS } from './useTTS';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { stripMarkdown } from './markdownStripper';
import { EDGE_TTS_VOICES } from './voiceRegistry';

function TTSSettingsPanel() {
  const { tts, setTTS } = useSettingsStore();

  const voiceOptions = EDGE_TTS_VOICES.map((v) => ({ value: v.id, label: v.label }));

  return (
    <Flex vertical gap={14} style={{ width: 260 }}>
      <div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          音色
        </Typography.Text>
        <Select
          style={{ width: '100%', marginTop: 4 }}
          size="small"
          placeholder="晓晓（默认）"
          allowClear
          value={tts.voiceName ?? undefined}
          onChange={(v) => setTTS({ voiceName: v ?? null })}
          options={voiceOptions}
        />
      </div>
      <div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          语速：{tts.rate.toFixed(1)}x
        </Typography.Text>
        <Slider
          min={0.5}
          max={2}
          step={0.1}
          value={tts.rate}
          onChange={(v) => setTTS({ rate: v })}
          style={{ margin: '4px 0 0' }}
        />
      </div>
      <div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          音调：{tts.pitch.toFixed(1)}
        </Typography.Text>
        <Slider
          min={0.5}
          max={2}
          step={0.1}
          value={tts.pitch}
          onChange={(v) => setTTS({ pitch: v })}
          style={{ margin: '4px 0 0' }}
        />
      </div>
    </Flex>
  );
}

type TTSButtonProps = {
  messageId: string;
  content: string;
  btnStyle?: React.CSSProperties;
};

export function TTSButton({ messageId, content, btnStyle }: TTSButtonProps) {
  const cleanText = stripMarkdown(content);
  const { status, play, stop, supported } = useTTS(messageId, cleanText);

  if (!supported || !cleanText.trim()) return null;

  const isPlaying = status === 'playing';

  return (
    <Flex gap={1} align="center">
      <Tooltip title={isPlaying ? '停止朗读' : '朗读'}>
        <Button
          type="text"
          size="small"
          icon={isPlaying ? <StopOutlined /> : <SoundOutlined />}
          onClick={isPlaying ? stop : play}
          style={btnStyle}
        />
      </Tooltip>
      {!isPlaying && (
        <Popover
          trigger="click"
          content={<TTSSettingsPanel />}
          title="语音设置"
          placement="bottomRight"
          arrow={false}
        >
          <Tooltip title="语音设置">
            <Button
              type="text"
              size="small"
              icon={<SettingOutlined />}
              style={{ ...btnStyle, width: 20, fontSize: 11, opacity: 0.5 }}
            />
          </Tooltip>
        </Popover>
      )}
    </Flex>
  );
}
