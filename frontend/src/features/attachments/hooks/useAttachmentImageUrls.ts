import { useEffect, useRef, useState } from 'react';
import { downloadAttachment } from '@/features/attachments/attachmentService';
import type { AttachmentImageStatus, AttachmentPreview } from '@/features/attachments/types';

type ImageUrlState = { status: AttachmentImageStatus; url: string | null };

/**
 * 集中管理一组附件里图片类型的下载 & object URL 生命周期，
 * 供缩略图和大图预览（画廊模式）共用同一份状态，避免重复下载。
 */
export function useAttachmentImageUrls(attachments: AttachmentPreview[]): Record<string, ImageUrlState> {
  const [urls, setUrls] = useState<Record<string, ImageUrlState>>({});
  const objectUrlsRef = useRef<Record<string, string>>({});

  useEffect(() => {
    const imageAttachments = attachments.filter((att) => att.mimeType.startsWith('image/'));
    const currentIds = new Set(imageAttachments.map((att) => att.id));

    Object.entries(objectUrlsRef.current).forEach(([id, url]) => {
      if (!currentIds.has(id)) {
        URL.revokeObjectURL(url);
        delete objectUrlsRef.current[id];
      }
    });

    imageAttachments.forEach((att) => {
      if (objectUrlsRef.current[att.id]) return;
      setUrls((prev) => ({ ...prev, [att.id]: { status: 'loading', url: null } }));
      void downloadAttachment(att.id)
        .then((blob) => {
          const objectUrl = URL.createObjectURL(blob);
          objectUrlsRef.current[att.id] = objectUrl;
          setUrls((prev) => ({ ...prev, [att.id]: { status: 'ready', url: objectUrl } }));
        })
        .catch(() => {
          setUrls((prev) => ({ ...prev, [att.id]: { status: 'error', url: null } }));
        });
    });
  }, [attachments]);

  useEffect(() => {
    return () => {
      Object.values(objectUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
      objectUrlsRef.current = {};
    };
  }, []);

  return urls;
}
