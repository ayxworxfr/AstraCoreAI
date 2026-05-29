import { useEffect, useRef, useState } from 'react';

/**
 * 流式打字机动画 hook。
 *
 * 维护两个独立速率：
 *   - server pace：SSE delta 填充 target（由外部驱动）
 *   - animation pace：RAF 60fps 追赶 target → displayed（本 hook 驱动）
 *
 * 无论后端每次发多大 chunk，用户看到的永远是平滑逐字输出。
 *
 * @param target   当前完整内容（随 SSE 增长）
 * @param active   true = 正在流式生成；false = 生成结束，立刻 snap 到最终内容
 */
export function useTypewriter(target: string, active: boolean): string {
  const [displayed, setDisplayed] = useState('');
  // 将易变值放进 ref，避免 RAF tick 闭包捕获旧值，也避免每次 target 变化都重启 effect
  const ref = useRef({ target, displayed: '', rafId: null as number | null });

  // 每次 render 同步最新 target 到 ref（不触发 effect 重跑）
  ref.current.target = target;

  useEffect(() => {
    if (!active) {
      // 流结束：取消动画并立刻呈现最终内容
      if (ref.current.rafId !== null) {
        cancelAnimationFrame(ref.current.rafId);
        ref.current.rafId = null;
      }
      ref.current.displayed = ref.current.target;
      setDisplayed(ref.current.target);
      return;
    }

    // 新一轮流式开始时，displayed 可能残留上一条消息的内容（比 target 长），重置
    if (ref.current.displayed.length > ref.current.target.length) {
      ref.current.displayed = '';
      setDisplayed('');
    }

    // loop 已在运行，或尚无内容可显示时直接返回；等 target 增长后再次进入 effect
    if (ref.current.rafId !== null || ref.current.displayed.length >= ref.current.target.length) {
      return;
    }

    const tick = () => {
      const t = ref.current.target;
      const d = ref.current.displayed;

      if (d.length >= t.length) {
        // 追上 target，自我终止；等 target 继续增长时 effect 重启 loop
        ref.current.rafId = null;
        return;
      }

      // 自适应步进：积压多时快追，接近时放慢，保证视觉平滑
      const remaining = t.length - d.length;
      const step = Math.min(Math.max(Math.ceil(remaining / 8), 3), 30);
      const next = t.slice(0, d.length + step);

      ref.current.displayed = next;
      setDisplayed(next);
      ref.current.rafId = requestAnimationFrame(tick);
    };

    ref.current.rafId = requestAnimationFrame(tick);
  }, [active, target]); // target 变化（新 delta 到来）时重新检查是否需要重启 loop

  // 组件卸载时取消挂起的 RAF
  useEffect(() => {
    return () => {
      if (ref.current.rafId !== null) {
        cancelAnimationFrame(ref.current.rafId);
      }
    };
  }, []);

  return displayed;
}
