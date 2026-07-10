export type AttachmentUploadResponse = {
  attachment_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
};

export type AttachmentPreview = {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
};

export type AttachmentImageStatus = 'loading' | 'ready' | 'error';
