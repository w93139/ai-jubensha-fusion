import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';

/** Both sizes use the same authenticated blob and verified orientation. */
export default function PlayImageView({ url, label, rotation, onRotate, zoom, onZoom }: {
  url: string; label: string; rotation: number; onRotate: () => void;
  zoom: number; onZoom: () => void;
}) {
  const picture = () => (
    // Local authenticated blob; never send private images to an image proxy.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt={label} className="absolute inset-0 h-full w-full object-contain" style={{ transform: `rotate(${rotation}deg)` }} />
  );
  return <Dialog.Root>
    <Dialog.Trigger asChild><button type="button" aria-label={`放大${label}`} className="relative mx-auto block aspect-square w-full max-w-[60dvh] overflow-hidden rounded bg-ink/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brass">{picture()}</button></Dialog.Trigger>
    <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-mist"><button type="button" className="min-h-11 rounded border border-line px-3 py-2 text-brass hover:bg-raised" onClick={onRotate} aria-label={`旋转${label}`}>旋转图片</button><span>点击图片放大阅读</span></div>
    <Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-[90] bg-black/80" />
      <Dialog.Content className="fixed inset-3 z-[90] flex flex-col rounded-xl border border-line bg-panel p-3 text-paper shadow-xl sm:p-5">
        <div className="flex shrink-0 items-center gap-3"><Dialog.Title className="min-w-0 flex-1 text-base font-semibold text-brass">{label}</Dialog.Title>
          <button type="button" className="min-h-11 shrink-0 rounded border border-brass/50 px-3 text-sm text-brass" onClick={onZoom}>{zoom === 1 ? '放大细节' : '适应窗口'}</button>
          <Dialog.Close asChild><button type="button" aria-label="关闭图片" className="min-h-11 shrink-0 rounded px-3 text-brass"><X className="h-5 w-5" aria-hidden="true" /></button></Dialog.Close>
        </div>
        <Dialog.Description className="mb-2 shrink-0 text-xs text-mist">可放大查看细节；放大后滑动阅读。</Dialog.Description>
        <div className="min-h-0 flex-1 overflow-auto overscroll-contain rounded bg-ink/50">
          <div className="relative mx-auto aspect-square" style={{ width: zoom === 1 ? '100%' : 'min(200%, 144dvh)', maxWidth: zoom === 1 ? '72dvh' : undefined }}>{picture()}</div>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
