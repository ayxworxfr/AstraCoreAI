import { useCallback, useEffect, useRef } from 'react';
import { useTTSStore } from './ttsStore';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { splitIntoSentences } from './sentenceSplitter';
import { resolveVoice } from './voiceRegistry';
import type { TTSStatus } from './ttsStore';

export const isSpeechSupported =
  typeof window !== 'undefined' && 'speechSynthesis' in window;

export type UseTTSReturn = {
  status: TTSStatus;
  play: () => void;
  stop: () => void;
  supported: boolean;
};

/**
 * Manages Web Speech API playback for one chat message.
 *
 * Uses a ref-based speakNext pattern so that onend callbacks always call the
 * latest version of the function without stale closure issues.
 *
 * Only one message can play at a time — calling play() cancels any ongoing
 * speech first, which triggers the playing message's onend to notice the
 * store state change and stop re-queuing.
 */
export function useTTS(messageId: string, text: string): UseTTSReturn {
  const { activeMessageId, status, setActive, clearActive } = useTTSStore();
  const isActive = activeMessageId === messageId;
  const currentStatus: TTSStatus = isActive ? status : 'idle';

  const queueRef = useRef<string[]>([]);
  const cancelTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Stable ref: always points to the latest speakNext implementation.
  // Using a ref avoids stale closures in utter.onend without adding
  // speakNext itself to dependency arrays.
  const speakNextRef = useRef<() => void>(() => {});
  speakNextRef.current = () => {
    if (queueRef.current.length === 0) {
      useTTSStore.getState().clearActive();
      return;
    }
    const sentence = queueRef.current.shift()!;
    const { tts } = useSettingsStore.getState();

    const utter = new SpeechSynthesisUtterance(sentence);
    utter.rate = tts.rate;
    utter.pitch = tts.pitch;
    const voice = resolveVoice(tts.voiceName);
    if (voice) utter.voice = voice;

    utter.onend = () => {
      const state = useTTSStore.getState();
      // Guard: only continue if this message is still the active one.
      if (state.activeMessageId === messageId && state.status === 'playing') {
        speakNextRef.current();
      }
    };
    utter.onerror = () => {
      useTTSStore.getState().clearActive();
    };

    speechSynthesis.speak(utter);
  };

  const play = useCallback(() => {
    if (!isSpeechSupported) return;
    // Cancel any currently playing speech across all messages.
    speechSynthesis.cancel();
    if (cancelTimerRef.current) {
      clearTimeout(cancelTimerRef.current);
      cancelTimerRef.current = null;
    }

    const sentences = splitIntoSentences(text);
    if (!sentences.length) return;

    queueRef.current = [...sentences];
    setActive(messageId);

    // Small delay: Chrome requires cancel() to fully settle before speak().
    cancelTimerRef.current = setTimeout(() => {
      cancelTimerRef.current = null;
      speakNextRef.current();
    }, 80);
  }, [text, messageId, setActive]);

  const stop = useCallback(() => {
    if (!isActive) return;
    if (cancelTimerRef.current) {
      clearTimeout(cancelTimerRef.current);
      cancelTimerRef.current = null;
    }
    speechSynthesis.cancel();
    queueRef.current = [];
    clearActive();
  }, [isActive, clearActive]);

  // Clear local queue when another message takes over.
  useEffect(() => {
    if (!isActive) {
      queueRef.current = [];
    }
  }, [isActive]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (cancelTimerRef.current) clearTimeout(cancelTimerRef.current);
      if (useTTSStore.getState().activeMessageId === messageId) {
        speechSynthesis.cancel();
        useTTSStore.getState().clearActive();
      }
    };
  }, [messageId]);

  return { status: currentStatus, play, stop, supported: isSpeechSupported };
}
