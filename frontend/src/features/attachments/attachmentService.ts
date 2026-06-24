import { apiClient } from '@/shared/services/apiClient';
import type { AttachmentUploadResponse } from '@/features/attachments/types';

export async function uploadAttachment(file: File): Promise<AttachmentUploadResponse> {
  const formData = new FormData();
  const filename = file.name || `paste.${file.type.split('/')[1] || 'bin'}`;
  formData.append('file', file, filename);
  const { data } = await apiClient.post<AttachmentUploadResponse>('/api/v1/attachments', formData);
  return data;
}

export async function downloadAttachment(attachmentId: string): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(`/api/v1/attachments/${attachmentId}`, {
    responseType: 'blob',
  });
  return data;
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  await apiClient.delete(`/api/v1/attachments/${attachmentId}`);
}
