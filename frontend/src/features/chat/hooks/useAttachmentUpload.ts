import { useState, useRef, useCallback } from 'react';
import { message as antdMessage } from 'antd';
import { uploadAttachment, deleteAttachment } from '@/features/attachments/attachmentService';
import { useChatStore } from '@/features/chat/store/chatStore';
import { normalizeError } from '@/shared/services/apiClient';

const ACCEPTED_ATTACHMENT_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp',
  'application/pdf',
]);

// 剪贴板粘贴的图片浏览器统一给的都是 "image.png" 这类固定名字，
// 用递增序号 + 时间戳生成唯一文件名，避免多张粘贴图重名混淆
let pastedImageSeq = 0;

function buildPastedFilename(mimeType: string): string {
  pastedImageSeq += 1;
  const ext = mimeType.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `image-${stamp}-${pastedImageSeq}.${ext}`;
}

export function useAttachmentUpload({ attachmentDisabled }: { attachmentDisabled: boolean }) {
  const { addAttachment, removeAttachment } = useChatStore();
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({});
  const [uploadingCount, setUploadingCount] = useState(0);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const dragDepthRef = useRef(0);

  const clearPreviewUrls = useCallback(() => {
    setPreviewUrls((prev) => {
      Object.values(prev).forEach((url) => URL.revokeObjectURL(url));
      return {};
    });
  }, []);

  const filterUploadableFiles = useCallback((files: File[]) => {
    const accepted = files.filter((file) => ACCEPTED_ATTACHMENT_TYPES.has(file.type));
    if (accepted.length !== files.length) {
      void antdMessage.warning('仅支持上传图片或 PDF 附件');
    }
    return accepted;
  }, []);

  const uploadFiles = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    const uploadable = filterUploadableFiles(files);
    if (uploadable.length === 0) return;
    setUploadingCount((c) => c + uploadable.length);
    await Promise.all(uploadable.map(async (file) => {
      try {
        const res = await uploadAttachment(file);
        const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
        addAttachment({ id: res.attachment_id, filename: res.filename, mimeType: res.mime_type, sizeBytes: res.size_bytes });
        if (previewUrl) setPreviewUrls((prev) => ({ ...prev, [res.attachment_id]: previewUrl }));
      } catch (err) {
        void antdMessage.error(`附件上传失败：${normalizeError(err)}`);
      } finally {
        setUploadingCount((c) => c - 1);
      }
    }));
  }, [addAttachment, filterUploadableFiles]);

  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = '';
    await uploadFiles(files);
  }, [uploadFiles]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    if (attachmentDisabled) return;
    const imageFiles = Array.from(e.clipboardData.items)
      .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null)
      // 粘贴的文件浏览器默认名都叫 image.png，重新命名成带时间戳的唯一英文名
      .map((file) => new File([file], buildPastedFilename(file.type), { type: file.type }));
    if (imageFiles.length === 0) return;
    void uploadFiles(imageFiles);
  }, [attachmentDisabled, uploadFiles]);

  const hasDraggedFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes('Files');

  const handleDragEnter = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (attachmentDisabled) return;
    dragDepthRef.current += 1;
    setDraggingFiles(true);
  }, [attachmentDisabled]);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (!attachmentDisabled) e.dataTransfer.dropEffect = 'copy';
  }, [attachmentDisabled]);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDraggingFiles(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepthRef.current = 0;
    setDraggingFiles(false);
    if (attachmentDisabled) return;
    void uploadFiles(Array.from(e.dataTransfer.files));
  }, [attachmentDisabled, uploadFiles]);

  const handleRemoveAttachment = useCallback((id: string) => {
    removeAttachment(id);
    const url = previewUrls[id];
    if (url) {
      URL.revokeObjectURL(url);
      setPreviewUrls((prev) => { const next = { ...prev }; delete next[id]; return next; });
    }
    void deleteAttachment(id).catch(() => undefined);
  }, [removeAttachment, previewUrls]);

  return {
    previewUrls,
    uploadingCount,
    draggingFiles,
    clearPreviewUrls,
    handleFileChange,
    handlePaste,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handleRemoveAttachment,
  };
}
