import { useState, useEffect } from 'react';

export type VoiceOption = {
  name: string;
  lang: string;
  localService: boolean;
};

/** React hook that returns available voices, re-rendering when they load. */
export function useAvailableVoices(): VoiceOption[] {
  const [voices, setVoices] = useState<VoiceOption[]>([]);

  useEffect(() => {
    const update = () => {
      setVoices(
        speechSynthesis.getVoices().map((v) => ({
          name: v.name,
          lang: v.lang,
          localService: v.localService,
        })),
      );
    };
    // Chrome loads voices asynchronously; call immediately then listen for changes.
    update();
    speechSynthesis.addEventListener('voiceschanged', update);
    return () => speechSynthesis.removeEventListener('voiceschanged', update);
  }, []);

  return voices;
}

/**
 * Resolve a concrete SpeechSynthesisVoice from a stored name.
 *
 * Falls back to: Microsoft Chinese → any Chinese → any English → first available.
 */
export function resolveVoice(voiceName: string | null): SpeechSynthesisVoice | null {
  if (typeof window === 'undefined' || !window.speechSynthesis) return null;
  const voices = speechSynthesis.getVoices();
  if (!voices.length) return null;

  if (voiceName) {
    const match = voices.find((v) => v.name === voiceName);
    if (match) return match;
  }

  const chinese = voices.filter((v) => v.lang.startsWith('zh'));
  return (
    chinese.find((v) => v.name.startsWith('Microsoft')) ??
    chinese[0] ??
    voices.find((v) => v.lang.startsWith('en')) ??
    voices[0] ??
    null
  );
}
