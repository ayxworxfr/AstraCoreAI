import { useCallback, useEffect, useRef } from 'react';
import { useTTSStore } from './ttsStore';
import { useSettingsStore } from '@/features/settings/store/settingsStore';
import { resolveVoiceId } from './voiceRegistry';
import { getAuthHeaders } from '@/shared/services/apiClient';
import type { TTSStatus } from './ttsStore';

const _API_BASE = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * MediaSource streaming with audio/mpeg is supported on desktop Chrome/Edge/Firefox
 * and Android Chrome, but not on iOS Safari (returns false for audio/mpeg).
 * The blob fallback path is used when this is false.
 */
const _MSE_SUPPORTED =
  typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported('audio/mpeg');

export type UseTTSReturn = {
  status: TTSStatus;
  play: () => void;
  stop: () => void;
  supported: boolean;
};

/**
 * Manages Edge TTS playback for one chat message via the backend proxy.
 *
 * Two paths depending on browser support:
 *  - MSE (MediaSource): streams audio/mpeg chunks via fetch ReadableStream,
 *    starts playing on first buffered chunk.
 *  - Blob fallback (iOS / no MSE): downloads the full response, then plays.
 *
 * Only one message plays at a time — the store's activeMessageId enforces this.
 */
export function useTTS(messageId: string, text: string): UseTTSReturn {
  const { activeMessageId, status, setLoading, clearActive } = useTTSStore();
  const isActive = activeMessageId === messageId;
  const currentStatus: TTSStatus = isActive ? status : 'idle';

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Tracks a freshly-created URL that we must revoke on cleanup.
  const ownedUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    if (ownedUrlRef.current) {
      URL.revokeObjectURL(ownedUrlRef.current);
      ownedUrlRef.current = null;
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
    const voice = resolveVoiceId(tts.voiceName);
    const { rate, pitch } = tts;
    const truncatedText = text.slice(0, 8000);
    const ab = new AbortController();
    abortRef.current = ab;

    setLoading(messageId);

    const fetchAudio = () =>
      fetch(`${_API_BASE}/api/v1/tts/synthesize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ text: truncatedText, voice, rate, pitch }),
        signal: ab.signal,
      });

    if (_MSE_SUPPORTED) {
      void (async () => {
        const mediaSource = new MediaSource();
        const mseUrl = URL.createObjectURL(mediaSource);
        ownedUrlRef.current = mseUrl;

        const audio = new Audio(mseUrl);
        audioRef.current = audio;

        audio.onplay = () => useTTSStore.getState().setPlaying(messageId);

        // Revoke the MSE URL and clear state when audio finishes or errors.
        // Also aborts any in-flight download so we don't waste bandwidth.
        const onDone = () => {
          ab.abort();
          audioRef.current = null;
          if (ownedUrlRef.current) {
            URL.revokeObjectURL(ownedUrlRef.current);
            ownedUrlRef.current = null;
          }
          useTTSStore.getState().clearActive();
        };
        audio.onended = onDone;
        audio.onerror = onDone;

        // Start buffering immediately; browser plays once enough data is available.
        audio.play().catch(onDone);

        await new Promise<void>((resolve) =>
          mediaSource.addEventListener('sourceopen', () => resolve(), { once: true }),
        );
        if (ab.signal.aborted) return;

        const sb = mediaSource.addSourceBuffer('audio/mpeg');
        const appendQueue: Uint8Array<ArrayBuffer>[] = [];
        let streamDone = false;

        // Append the next queued chunk only when SourceBuffer is not updating.
        const flush = () => {
          if (sb.updating || appendQueue.length === 0) return;
          sb.appendBuffer(appendQueue.shift()!);
        };

        // Signal end-of-stream once all chunks are appended.
        const tryEndStream = () => {
          if (!streamDone || appendQueue.length > 0 || sb.updating) return;
          try {
            mediaSource.endOfStream();
          } catch {
            // MediaSource may already be closed if the audio element was detached.
          }
        };

        sb.addEventListener('updateend', () => {
          flush();
          tryEndStream();
        });

        try {
          const response = await fetchAudio();
          if (!response.ok) throw new Error(`TTS ${response.status}`);

          const reader = response.body!.getReader();
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            if (ab.signal.aborted) break;
            // Fetch stream chunks are always ArrayBuffer-backed; cast is safe.
            const chunk = value as Uint8Array<ArrayBuffer>;
            appendQueue.push(chunk);
            flush();
          }
        } catch (err) {
          if (!ab.signal.aborted) {
            console.error('[TTS] stream error', err);
            // Signals a network error to the audio element, which fires onerror → onDone.
            try {
              mediaSource.endOfStream('network');
            } catch {
              // Ignore if already closed.
            }
          }
          return;
        }

        streamDone = true;
        tryEndStream();
      })();
    } else {
      // Blob fallback: collect the full response before playing (iOS Safari).
      void (async () => {
        try {
          const response = await fetchAudio();
          if (!response.ok) throw new Error(`TTS ${response.status}`);
          if (ab.signal.aborted) return;

          const blob = await response.blob();
          if (ab.signal.aborted) return;

          const blobUrl = URL.createObjectURL(blob);
          ownedUrlRef.current = blobUrl;

          const audio = new Audio(blobUrl);
          audioRef.current = audio;

          audio.onplay = () => useTTSStore.getState().setPlaying(messageId);
          const onDone = () => {
            audioRef.current = null;
            if (ownedUrlRef.current) {
              URL.revokeObjectURL(ownedUrlRef.current);
              ownedUrlRef.current = null;
            }
            useTTSStore.getState().clearActive();
          };
          audio.onended = onDone;
          audio.onerror = onDone;

          audio.play().catch(onDone);
        } catch (err) {
          if (!ab.signal.aborted) {
            console.error('[TTS] blob error', err);
            useTTSStore.getState().clearActive();
          }
        }
      })();
    }
  }, [text, messageId, setLoading, cleanup]);

  // When another message takes over, abort and cleanup this one.
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
