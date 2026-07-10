import { Button, Flex, Spin, Tooltip, Typography, theme } from 'antd';
import { DeleteOutlined, FilePdfOutlined, PictureOutlined } from '@ant-design/icons';
import type { AttachmentImageStatus, AttachmentPreview } from '@/features/attachments/types';
import { formatBytes } from '@/shared/utils/format';

type Props = {
  attachment: AttachmentPreview;
  imageUrl?: string | null;
  imageStatus?: AttachmentImageStatus;
  size?: 'compact' | 'regular';
  align?: 'left' | 'right';
  onPreview?: () => void;
  onRemove?: (id: string) => void;
};

export default function AttachmentPreviewCard({
  attachment,
  imageUrl,
  imageStatus,
  size = 'regular',
  align = 'left',
  onPreview,
  onRemove,
}: Props): JSX.Element {
  const { token } = theme.useToken();
  const isImage = attachment.mimeType.startsWith('image/');
  const isPdf = attachment.mimeType === 'application/pdf';
  const status: AttachmentImageStatus = imageStatus ?? (imageUrl ? 'ready' : 'loading');
  const canPreview = isImage && status === 'ready' && !!imageUrl && !!onPreview;
  const mediaSize = size === 'compact' ? 28 : 44;
  const maxWidth = size === 'compact' ? 180 : 220;
  const filename = size === 'compact' && attachment.filename.length > 20
    ? `${attachment.filename.slice(0, 18)}...`
    : attachment.filename;

  const handlePreview = () => {
    if (canPreview) onPreview();
  };

  return (
    <Flex
      align="center"
      gap={size === 'compact' ? 6 : 8}
      onClick={handlePreview}
      style={{
        width: size === 'compact' ? undefined : maxWidth,
        maxWidth,
        padding: size === 'compact' ? '4px 8px' : 6,
        borderRadius: size === 'compact' ? 8 : 12,
        cursor: canPreview ? 'zoom-in' : 'default',
        background: size === 'compact' ? token.colorFillSecondary : 'rgba(255, 255, 255, 0.72)',
        border: `1px solid ${token.colorBorderSecondary}`,
        boxShadow: size === 'compact' ? undefined : 'inset 0 1px 0 rgba(255, 255, 255, 0.72)',
        textAlign: align,
      }}
    >
      {isImage && status === 'loading' ? (
        <Flex
          align="center"
          justify="center"
          style={{
            width: mediaSize,
            height: mediaSize,
            borderRadius: size === 'compact' ? 4 : 9,
            flexShrink: 0,
            background: token.colorFillSecondary,
          }}
        >
          <Spin size="small" />
        </Flex>
      ) : imageUrl && status === 'ready' ? (
        <img
          src={imageUrl}
          alt={attachment.filename}
          style={{
            width: mediaSize,
            height: mediaSize,
            objectFit: 'cover',
            borderRadius: size === 'compact' ? 4 : 9,
            flexShrink: 0,
          }}
        />
      ) : (
        <Flex
          align="center"
          justify="center"
          style={{
            width: mediaSize,
            height: mediaSize,
            borderRadius: size === 'compact' ? 4 : 9,
            flexShrink: 0,
            color: isPdf ? token.colorError : token.colorTextSecondary,
            background: token.colorFillSecondary,
            fontSize: size === 'compact' ? 16 : 20,
          }}
        >
          {isPdf ? <FilePdfOutlined /> : <PictureOutlined />}
        </Flex>
      )}

      <Flex vertical gap={size === 'compact' ? 0 : 2} style={{ minWidth: 0, flex: 1 }}>
        <Typography.Text ellipsis style={{ fontSize: 12, lineHeight: size === 'compact' ? 1.3 : 1.25 }}>
          {filename}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11, lineHeight: 1.2 }}>
          {size === 'compact' ? formatBytes(attachment.sizeBytes) : `${isImage ? '图片' : isPdf ? 'PDF' : '附件'} · ${formatBytes(attachment.sizeBytes)}`}
        </Typography.Text>
      </Flex>

      {onRemove && (
        <Tooltip title="移除附件">
          <Button
            type="text"
            size="small"
            icon={<DeleteOutlined />}
            onClick={(event) => {
              event.stopPropagation();
              onRemove(attachment.id);
            }}
            style={{
              color: token.colorTextTertiary,
              padding: 0,
              width: 20,
              height: 20,
              flexShrink: 0,
            }}
          />
        </Tooltip>
      )}
    </Flex>
  );
}
