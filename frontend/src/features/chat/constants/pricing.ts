/**
 * 模型定价表（USD/百万 token）。
 * 价格随官方调整时在此处同步更新。
 * 使用标准 cache-miss 费率（不含 prompt caching 折扣）。
 * 最后更新：2026-06
 */

type ModelPricing = {
  inputPerMillion: number;
  outputPerMillion: number;
  displayName: string;
};

const MODEL_PRICING: Record<string, ModelPricing> = {
  // ── Anthropic Claude ──────────────────────────────────────────────────────
  'claude-opus-4-8':           { inputPerMillion: 5,    outputPerMillion: 25,  displayName: 'Opus 4.8'   },
  'claude-opus-4-7':           { inputPerMillion: 5,    outputPerMillion: 25,  displayName: 'Opus 4.7'   },
  'claude-opus-4-6':           { inputPerMillion: 5,    outputPerMillion: 25,  displayName: 'Opus 4.6'   },
  'claude-sonnet-4-6':         { inputPerMillion: 3,    outputPerMillion: 15,  displayName: 'Sonnet 4.6' },
  'claude-sonnet-4-5':         { inputPerMillion: 3,    outputPerMillion: 15,  displayName: 'Sonnet 4.5' },
  'claude-haiku-4-5':          { inputPerMillion: 1,    outputPerMillion: 5,   displayName: 'Haiku 4.5'  },
  'claude-3-5-sonnet':         { inputPerMillion: 3,    outputPerMillion: 15,  displayName: 'Sonnet 3.5' },
  'claude-3-5-haiku':          { inputPerMillion: 0.8,  outputPerMillion: 4,   displayName: 'Haiku 3.5'  },
  'claude-3-opus':             { inputPerMillion: 15,   outputPerMillion: 75,  displayName: 'Opus 3'     },

  // ── DeepSeek ──────────────────────────────────────────────────────────────
  // deepseek-chat / deepseek-reasoner 将于 2026-07-24 废弃，映射到 v4-flash
  'deepseek-v4-pro':           { inputPerMillion: 0.435, outputPerMillion: 0.87, displayName: 'DS V4 Pro'   },
  'deepseek-v4-flash':         { inputPerMillion: 0.14,  outputPerMillion: 0.28, displayName: 'DS V4 Flash' },
  'deepseek-chat':             { inputPerMillion: 0.14,  outputPerMillion: 0.28, displayName: 'DS Chat'     },
  'deepseek-reasoner':         { inputPerMillion: 0.14,  outputPerMillion: 0.28, displayName: 'DS Reasoner' },

  // ── Z.AI / Zhipu GLM ──────────────────────────────────────────────────────
  'glm-5.1':                   { inputPerMillion: 1.4,  outputPerMillion: 4.4,  displayName: 'GLM-5.1'      },
  'glm-5-turbo':               { inputPerMillion: 1.2,  outputPerMillion: 4.0,  displayName: 'GLM-5 Turbo'  },
  'glm-5':                     { inputPerMillion: 1.0,  outputPerMillion: 3.2,  displayName: 'GLM-5'        },
  'glm-4.7-flashx':            { inputPerMillion: 0.07, outputPerMillion: 0.4,  displayName: 'GLM-4.7 FlashX' },
  'glm-4.5-air':               { inputPerMillion: 0.2,  outputPerMillion: 1.1,  displayName: 'GLM-4.5 Air'  },
  'glm-4.7-flash':             { inputPerMillion: 0,    outputPerMillion: 0,    displayName: 'GLM-4.7 Flash' },

  // ── OpenAI GPT ────────────────────────────────────────────────────────────
  'gpt-5.5':                   { inputPerMillion: 5,    outputPerMillion: 30,   displayName: 'GPT-5.5'      },
  'gpt-4.1':                   { inputPerMillion: 2,    outputPerMillion: 8,    displayName: 'GPT-4.1'      },
  'gpt-4.1-mini':              { inputPerMillion: 0.4,  outputPerMillion: 1.6,  displayName: 'GPT-4.1 Mini' },
  'gpt-4.1-nano':              { inputPerMillion: 0.1,  outputPerMillion: 0.4,  displayName: 'GPT-4.1 Nano' },
  'gpt-4o':                    { inputPerMillion: 2.5,  outputPerMillion: 10,   displayName: 'GPT-4o'       },
  'gpt-4o-mini':               { inputPerMillion: 0.15, outputPerMillion: 0.6,  displayName: 'GPT-4o Mini'  },
  'o4-mini':                   { inputPerMillion: 0.55, outputPerMillion: 2.2,  displayName: 'o4-mini'      },
  'o3':                        { inputPerMillion: 2,    outputPerMillion: 8,    displayName: 'o3'           },
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
