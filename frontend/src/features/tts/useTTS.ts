import { useCallback, useEffect, useRef } from 'react';
import { useTTSStore } from './ttsStore';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { DEFAULT_VOICE_ID } from './voiceRegistry';
import { apiClient } from '@/shared/services/apiClient';
import type { TTSStatus } from './ttsStore';

export type UseTTSReturn = {
  status: TTSStatus;
  play: () => void;
  stop: () => void;
  supported: boolean;
};

/**
 * Manages Edge TTS playback for one chat message via the backend proxy.
 *
 * Fetches an audio/mpeg blob from /api/v1/tts/synthesize and plays it
 * with an HTMLAudioElement. AbortController cancels in-flight requests
 * when stop() is called or another message takes over.
 *
 * Only one message can play at a time — calling play() on a second message
 * updates the store's activeMessageId, which causes the first message's
 * useEffect to abort its request and stop its audio element.
 */
export function useTTS(messageId: string, text: string): UseTTSReturn {
  const { activeMessageId, status, setActive, clearActive } = useTTSStore();
  const isActive = activeMessageId === messageId;
  const currentStatus: TTSStatus = isActive ? status : 'idle';

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    if (!isActive) return;
    cleanup();
    clearActive();
  }, [isActive, cleanup, clearActive]);

  const play = useCallback(() => {
    cleanup();

    const { tts } = useSettingsStore.getState();
    const ab = new AbortController();
    abortRef.current = ab;
    setActive(messageId);

    apiClient
      .post<Blob>(
        '/api/v1/tts/synthesize',
        {
          text: text.slice(0, 8000),
          voice: tts.voiceName ?? DEFAULT_VOICE_ID,
          rate: tts.rate,
          pitch: tts.pitch,
        },
        { responseType: 'blob', signal: ab.signal },
      )
      .then((response) => {
        if (ab.signal.aborted) return;

        const url = URL.createObjectURL(response.data);
        blobUrlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;

        audio.onended = () => {
          URL.revokeObjectURL(url);
          blobUrlRef.current = null;
          audioRef.current = null;
          useTTSStore.getState().clearActive();
        };
        audio.onerror = () => {
          URL.revokeObjectURL(url);
          blobUrlRef.current = null;
          audioRef.current = null;
          useTTSStore.getState().clearActive();
        };

        audio.play().catch(() => {
          cleanup();
          useTTSStore.getState().clearActive();
        });
      })
      .catch((err: unknown) => {
        if (ab.signal.aborted) return;
        console.error('[TTS] synthesize failed', err);
        cleanup();
        useTTSStore.getState().clearActive();
      });
  }, [text, messageId, setActive, cleanup]);

  // When another message takes over, abort this message's request and audio.
  useEffect(() => {
    if (!isActive) {
      cleanup();
    }
  }, [isActive, cleanup]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (useTTSStore.getState().activeMessageId === messageId) {
        cleanup();
        useTTSStore.getState().clearActive();
      } else {
        cleanup();
      }
    };
  }, [messageId, cleanup]);

  return { status: currentStatus, play, stop, supported: true };
}
