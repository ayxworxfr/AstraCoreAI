import { ApiOutlined } from '@ant-design/icons';
import { Button, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import { useEffect, useState } from 'react';
import { apiClient } from '@/shared/services/apiClient';
import { useChatStore } from '@/features/chat/store/chatStore';
import type { SystemInfo } from '@/features/system/types';

type ModelProfile = SystemInfo['llm']['profiles'][number];

export default function ModelSelector({ disabled }: { disabled: boolean }): JSX.Element | null {
  const { activeModelId, setActiveModelId, setThinkingMode } = useChatStore();
  const [llm, setLlm] = useState<SystemInfo['llm'] | null>(null);

  useEffect(() => {
    apiClient
      .get<SystemInfo>('/api/v1/system/')
      .then(({ data }) => setLlm(data.llm))
      .catch(() => {});
  }, []);

  if (!llm || llm.profiles.length <= 1) return null;

  const defaultProfile = llm.default_profile;
  const profilesById = new Map(llm.profiles.map((profile) => [profile.id, profile]));
  const selectedProfile = profilesById.get(activeModelId ?? defaultProfile) ?? profilesById.get(defaultProfile);

  const formatProfile = (profile: ModelProfile) => (
    profile.label || `${profile.protocol} / ${profile.model}`
  );

  const items: MenuProps['items'] = llm.profiles.map((profile) => ({
    key: profile.id,
    label: profile.id === defaultProfile ? `${formatProfile(profile)}（默认）` : formatProfile(profile),
  }));

  const displayName = selectedProfile ? formatProfile(selectedProfile) : defaultProfile;

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    setActiveModelId(key === defaultProfile ? null : key);
    const prevThinkingCtrl = selectedProfile?.controls.find((c) => c.kind === 'thinking');
    const newProfile = profilesById.get(key);
    const thinkingCtrl = newProfile?.controls.find((c) => c.kind === 'thinking');
    if (!thinkingCtrl) {
      // new model doesn't support thinking at all
      setThinkingMode('off');
    } else if (!prevThinkingCtrl) {
      // switching from a non-thinking model → restore new model's default
      setThinkingMode(thinkingCtrl.default as 'off' | 'on' | 'adaptive');
    } else {
      const currentMode = useChatStore.getState().thinkingMode;
      if (!(thinkingCtrl.modes as string[]).includes(currentMode)) {
        // current mode not valid for new model → use new model's default
        setThinkingMode(thinkingCtrl.default as 'off' | 'on' | 'adaptive');
      }
      // current mode is supported by new model → preserve it
    }
  };

  return (
    <Dropdown
      menu={{ items, onClick: handleMenuClick, selectedKeys: [activeModelId ?? defaultProfile] }}
      disabled={disabled}
    >
      <Button size="small" type="text" icon={<ApiOutlined />}>
        {displayName}
      </Button>
    </Dropdown>
  );
}
