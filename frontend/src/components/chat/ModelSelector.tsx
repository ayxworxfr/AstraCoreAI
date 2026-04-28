import { ApiOutlined } from '@ant-design/icons';
import { Button, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import { useEffect, useState } from 'react';
import { apiClient } from '../../services/apiClient';
import { useChatStore } from '../../stores/chatStore';
import type { SystemInfo } from '../../types/system';

type ModelProfile = SystemInfo['llm']['profiles'][number];

export default function ModelSelector({ disabled }: { disabled: boolean }): JSX.Element | null {
  const { activeModelId, setActiveModelId } = useChatStore();
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
    profile.label || `${profile.provider} / ${profile.model}`
  );

  const items: MenuProps['items'] = llm.profiles.map((profile) => ({
    key: profile.id,
    label: profile.id === defaultProfile ? `${formatProfile(profile)}（默认）` : formatProfile(profile),
  }));

  const displayName = selectedProfile ? formatProfile(selectedProfile) : defaultProfile;

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    setActiveModelId(key === defaultProfile ? null : key);
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
