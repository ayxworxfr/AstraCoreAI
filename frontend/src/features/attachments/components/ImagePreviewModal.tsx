import { useEffect, useRef, useState } from 'react';
import { Button, Flex, Modal, Spin, Tooltip, Typography, theme } from 'antd';
import {
  DownloadOutlined,
  LeftOutlined,
  PictureOutlined,
  RightOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons';
import type { AttachmentImageStatus } from '@/features/attachments/types';

export type PreviewImage = {
  id: string;
  alt: string;
  src: string | null;
  status: AttachmentImageStatus;
};

type Props = {
  open: boolean;
  images: PreviewImage[];
  index: number;
  onIndexChange: (index: number) => void;
  onClose: () => void;
};

const MIN_SCALE = 1;
const MAX_SCALE = 4;
const SCALE_STEP = 0.4;

export default function ImagePreviewModal({ open, images, index, onIndexChange, onClose }: Props): JSX.Element {
  const { token } = theme.useToken();
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startX: number; startY: number; startOffset: { x: number; y: number } } | null>(null);

  const current = images[index] as PreviewImage | undefined;
  const hasPrev = index > 0;
  const hasNext = index < images.length - 1;
  const showNav = images.length > 1;

  // 切图或关闭时重置缩放/平移，避免带着上一张的视图状态
  useEffect(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  }, [index, open]);

  const clampScale = (next: number) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));

  const goPrev = () => {
    if (index > 0) onIndexChange(index - 1);
  };
  const goNext = () => {
    if (index < images.length - 1) onIndexChange(index + 1);
  };

  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') goPrev();
      else if (e.key === 'ArrowRight') goNext();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, index, images.length]);

  const handleWheel = (e: React.WheelEvent) => {
    if (!current || current.status !== 'ready') return;
    e.preventDefault();
    const next = clampScale(scale + (e.deltaY < 0 ? SCALE_STEP : -SCALE_STEP));
    setScale(next);
    if (next === MIN_SCALE) setOffset({ x: 0, y: 0 });
  };

  const handleZoomIn = () => setScale((s) => clampScale(s + SCALE_STEP));
  const handleZoomOut = () => {
    setScale((s) => {
      const next = clampScale(s - SCALE_STEP);
      if (next === MIN_SCALE) setOffset({ x: 0, y: 0 });
      return next;
    });
  };
  const handleResetZoom = () => {
    setScale(MIN_SCALE);
    setOffset({ x: 0, y: 0 });
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLImageElement>) => {
    if (scale <= MIN_SCALE) return;
    dragRef.current = { startX: e.clientX, startY: e.clientY, startOffset: offset };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const handlePointerMove = (e: React.PointerEvent<HTMLImageElement>) => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    setOffset({ x: dragRef.current.startOffset.x + dx, y: dragRef.current.startOffset.y + dy });
  };
  const handlePointerUp = () => {
    dragRef.current = null;
    setDragging(false);
  };

  const handleDownload = () => {
    if (!current?.src) return;
    const link = document.createElement('a');
    link.href = current.src;
    link.download = current.alt || 'image';
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  return (
    <Modal
      open={open}
      footer={null}
      centered
      width="min(920px, calc(100vw - 32px))"
      title={
        <Flex align="center" gap={8}>
          <Typography.Text ellipsis style={{ maxWidth: 480 }}>
            {current?.alt || '图片预览'}
          </Typography.Text>
          {showNav && (
            <Typography.Text type="secondary" style={{ fontSize: 12, flexShrink: 0 }}>
              {index + 1} / {images.length}
            </Typography.Text>
          )}
        </Flex>
      }
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
      <div
        style={{
          position: 'relative',
          height: 'min(70vh, 640px)',
          background: token.colorBgLayout,
          overflow: 'hidden',
          touchAction: 'none',
        }}
        onWheel={handleWheel}
      >
        {current?.status === 'loading' && (
          <Flex align="center" justify="center" style={{ height: '100%' }}>
            <Spin />
          </Flex>
        )}
        {current?.status === 'error' && (
          <Flex
            vertical
            align="center"
            justify="center"
            gap={8}
            style={{ height: '100%', color: token.colorTextTertiary }}
          >
            <PictureOutlined style={{ fontSize: 32 }} />
            <Typography.Text type="secondary">图片加载失败</Typography.Text>
          </Flex>
        )}
        {current?.status === 'ready' && current.src && (
          <img
            src={current.src}
            alt={current.alt}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={handlePointerUp}
            draggable={false}
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              maxWidth: '100%',
              maxHeight: '100%',
              width: 'auto',
              height: 'auto',
              objectFit: 'contain',
              transform: `translate(-50%, -50%) translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
              transition: dragging ? 'none' : 'transform 0.12s ease-out',
              cursor: scale > MIN_SCALE ? (dragging ? 'grabbing' : 'grab') : 'default',
              userSelect: 'none',
            }}
          />
        )}

        {showNav && (
          <>
            <Button
              shape="circle"
              icon={<LeftOutlined />}
              disabled={!hasPrev}
              onClick={goPrev}
              aria-label="上一张"
              style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }}
            />
            <Button
              shape="circle"
              icon={<RightOutlined />}
              disabled={!hasNext}
              onClick={goNext}
              aria-label="下一张"
              style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)' }}
            />
          </>
        )}
      </div>

      <Flex
        align="center"
        justify="space-between"
        style={{ padding: '6px 16px', borderTop: `1px solid ${token.colorBorderSecondary}` }}
      >
        <Flex align="center" gap={4}>
          <Tooltip title="缩小">
            <Button
              type="text"
              size="small"
              icon={<ZoomOutOutlined />}
              disabled={current?.status !== 'ready' || scale <= MIN_SCALE}
              onClick={handleZoomOut}
            />
          </Tooltip>
          <Typography.Text type="secondary" style={{ fontSize: 12, width: 42, textAlign: 'center' }}>
            {Math.round(scale * 100)}%
          </Typography.Text>
          <Tooltip title="放大">
            <Button
              type="text"
              size="small"
              icon={<ZoomInOutlined />}
              disabled={current?.status !== 'ready' || scale >= MAX_SCALE}
              onClick={handleZoomIn}
            />
          </Tooltip>
          {scale > MIN_SCALE && (
            <Button type="text" size="small" onClick={handleResetZoom}>
              重置
            </Button>
          )}
        </Flex>
        <Tooltip title="下载原图">
          <Button
            type="text"
            size="small"
            icon={<DownloadOutlined />}
            disabled={current?.status !== 'ready'}
            onClick={handleDownload}
          />
        </Tooltip>
      </Flex>
    </Modal>
  );
}
