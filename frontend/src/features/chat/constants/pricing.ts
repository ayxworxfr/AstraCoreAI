/**
 * Anthropic 模型定价表（USD/百万 token）。
 * 价格随官方调整时在此处同步更新。
 */

type ModelPricing = {
  inputPerMillion: number;
  outputPerMillion: number;
  displayName: string;
};

const MODEL_PRICING: Record<string, ModelPricing> = {
  'claude-opus-4-7':           { inputPerMillion: 15,   outputPerMillion: 75,  displayName: 'Opus 4.7'   },
  'claude-sonnet-4-6':         { inputPerMillion: 3,    outputPerMillion: 15,  displayName: 'Sonnet 4.6' },
  'claude-haiku-4-5':          { inputPerMillion: 0.8,  outputPerMillion: 4,   displayName: 'Haiku 4.5'  },
  'claude-3-5-sonnet':         { inputPerMillion: 3,    outputPerMillion: 15,  displayName: 'Sonnet 3.5' },
  'claude-3-5-haiku':          { inputPerMillion: 0.8,  outputPerMillion: 4,   displayName: 'Haiku 3.5'  },
  'claude-3-opus':             { inputPerMillion: 15,   outputPerMillion: 75,  displayName: 'Opus 3'     },
};

const DEFAULT_PRICING: ModelPricing = { inputPerMillion: 3, outputPerMillion: 15, displayName: '' };

/** 按模型 ID 查找定价，优先精确匹配，其次前缀匹配。 */
export function getModelPricing(modelId: string): ModelPricing {
  if (!modelId) return DEFAULT_PRICING;
  if (MODEL_PRICING[modelId]) return MODEL_PRICING[modelId];
  // 前缀匹配：去掉末尾版本号（如 -20251001）后再匹配
  const baseId = modelId.replace(/-\d{8}$/, '');
  if (MODEL_PRICING[baseId]) return MODEL_PRICING[baseId];
  for (const [key, pricing] of Object.entries(MODEL_PRICING)) {
    if (modelId.startsWith(key) || baseId.startsWith(key)) return pricing;
  }
  return DEFAULT_PRICING;
}

/** 计算本次调用费用（USD）。 */
export function calculateCost(inputTokens: number, outputTokens: number, modelId: string): number {
  const p = getModelPricing(modelId);
  return (inputTokens * p.inputPerMillion + outputTokens * p.outputPerMillion) / 1_000_000;
}

/** 格式化 token 数量，≥1K 显示 K，≥1M 显示 M。 */
export function formatTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${Math.round(count / 1_000)}K`;
  return String(count);
}

/** 格式化美元费用，低于 $0.01 显示 <$0.01。 */
export function formatCost(usd: number): string {
  if (usd === 0) return '$0';
  if (usd < 0.01) return '<$0.01';
  if (usd < 10) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

/** 获取模型的展示名称。 */
export function getModelDisplayName(modelId: string): string {
  const p = getModelPricing(modelId);
  return p.displayName || modelId;
}
