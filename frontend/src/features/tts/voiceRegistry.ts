export type EdgeVoice = {
  id: string;
  label: string;
  lang: string;
  gender: 'female' | 'male';
};

/** 与 edge-tts 当前可用音色对齐；下线音色勿再写入。 */
export const EDGE_TTS_VOICES: EdgeVoice[] = [
  { id: 'zh-CN-XiaoxiaoNeural', label: '晓晓（普通话·女·温暖）', lang: 'zh-CN', gender: 'female' },
  { id: 'zh-CN-XiaoyiNeural', label: '晓伊（普通话·女·活泼）', lang: 'zh-CN', gender: 'female' },
  { id: 'zh-CN-liaoning-XiaobeiNeural', label: '晓北（东北·女）', lang: 'zh-CN', gender: 'female' },
  { id: 'zh-CN-shaanxi-XiaoniNeural', label: '晓妮（陕西·女）', lang: 'zh-CN', gender: 'female' },
  { id: 'zh-CN-YunxiNeural', label: '云希（普通话·男·年轻）', lang: 'zh-CN', gender: 'male' },
  { id: 'zh-CN-YunyangNeural', label: '云扬（普通话·男·新闻）', lang: 'zh-CN', gender: 'male' },
  { id: 'zh-CN-YunjianNeural', label: '云健（普通话·男·运动）', lang: 'zh-CN', gender: 'male' },
  { id: 'zh-CN-YunxiaNeural', label: '云夏（普通话·男）', lang: 'zh-CN', gender: 'male' },
  { id: 'zh-TW-HsiaoChenNeural', label: '晓臻（台湾·女）', lang: 'zh-TW', gender: 'female' },
  { id: 'zh-TW-HsiaoYuNeural', label: '晓雨（台湾·女）', lang: 'zh-TW', gender: 'female' },
  { id: 'zh-TW-YunJheNeural', label: '云哲（台湾·男）', lang: 'zh-TW', gender: 'male' },
  { id: 'zh-HK-HiuGaaiNeural', label: '曉佳（粤语·女）', lang: 'zh-HK', gender: 'female' },
  { id: 'zh-HK-HiuMaanNeural', label: '曉曼（粤语·女）', lang: 'zh-HK', gender: 'female' },
  { id: 'zh-HK-WanLungNeural', label: '雲龍（粤语·男）', lang: 'zh-HK', gender: 'male' },
  { id: 'en-US-AriaNeural', label: 'Aria（美式·女）', lang: 'en-US', gender: 'female' },
  { id: 'en-US-JennyNeural', label: 'Jenny（美式·女）', lang: 'en-US', gender: 'female' },
  { id: 'en-US-GuyNeural', label: 'Guy（美式·男）', lang: 'en-US', gender: 'male' },
  { id: 'en-GB-LibbyNeural', label: 'Libby（英式·女）', lang: 'en-GB', gender: 'female' },
  { id: 'en-GB-RyanNeural', label: 'Ryan（英式·男）', lang: 'en-GB', gender: 'male' },
];

export const DEFAULT_VOICE_ID = 'zh-CN-XiaoxiaoNeural';

const VALID_VOICE_IDS = new Set(EDGE_TTS_VOICES.map((voice) => voice.id));

/** 本地持久化可能残留已下线音色，统一回落到默认音色。 */
export function resolveVoiceId(voiceName: string | null | undefined): string {
  if (!voiceName || !VALID_VOICE_IDS.has(voiceName)) {
    return DEFAULT_VOICE_ID;
  }
  return voiceName;
}
