import { Modal, Typography, theme } from 'antd';

type Props = {
  open: boolean;
  src: string | null;
  alt: string;
  onClose: () => void;
};

export default function ImagePreviewModal({ open, src, alt, onClose }: Props): JSX.Element {
  const { token } = theme.useToken();

  return (
    <Modal
      open={open}
      footer={null}
      centered
      width="min(920px, calc(100vw - 32px))"
      title={<Typography.Text ellipsis>{alt || '图片预览'}</Typography.Text>}
      onCancel={onClose}
      styles={{
        body: {
          padding: 0,
          background: token.colorBgLayout,
          borderRadius: token.borderRadiusLG,
          overflow: 'hidden',
        },
      }}
    >
      {src && (
        <img
          src={src}
          alt={alt}
          style={{
            display: 'block',
            width: '100%',
            maxHeight: 'calc(100vh - 180px)',
            objectFit: 'contain',
            background: token.colorBgLayout,
          }}
        />
      )}
    </Modal>
  );
}
