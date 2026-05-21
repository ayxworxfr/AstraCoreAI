export const DEFAULT_TIME_ZONE = 'Asia/Shanghai';

type DateTimeParts = {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
};

function safeDate(value: string | Date): Date {
  if (value instanceof Date) return value;
  const text = value.trim();
  const hasTimeZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
  const isIsoDateTime = /^\d{4}-\d{2}-\d{2}T/.test(text);
  return new Date(isIsoDateTime && !hasTimeZone ? `${text}Z` : text);
}

function normalizeTimeZone(timeZone?: string): string {
  return timeZone?.trim() || DEFAULT_TIME_ZONE;
}

function getParts(value: string | Date, timeZone?: string): DateTimeParts {
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: normalizeTimeZone(timeZone),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  const parts = Object.fromEntries(
    formatter.formatToParts(safeDate(value)).map(({ type, value: partValue }) => [type, partValue]),
  );
  return {
    year: parts.year,
    month: parts.month,
    day: parts.day,
    hour: parts.hour,
    minute: parts.minute,
  };
}

function getDayKey(value: string | Date, timeZone?: string): string {
  const { year, month, day } = getParts(value, timeZone);
  return `${year}-${month}-${day}`;
}

function zonedStartOfDayMs(value: string | Date, timeZone?: string): number {
  const { year, month, day } = getParts(value, timeZone);
  return Date.UTC(Number(year), Number(month) - 1, Number(day));
}

export function formatAppDateTime(value: string | Date, timeZone?: string): string {
  const { year, month, day, hour, minute } = getParts(value, timeZone);
  return `${year}/${month}/${day} ${hour}:${minute}`;
}

export function formatAppMessageTime(value: string | Date, timeZone?: string): string {
  const { year, month, day, hour, minute } = getParts(value, timeZone);
  const now = getParts(new Date(), timeZone);
  const time = `${hour}:${minute}`;

  if (`${year}-${month}-${day}` === `${now.year}-${now.month}-${now.day}`) {
    return `今天 ${time}`;
  }
  if (year === now.year) {
    return `${Number(month)}月${Number(day)}日 ${time}`;
  }
  return `${year}年${Number(month)}月${Number(day)}日 ${time}`;
}

export function getAppTimeGroup(value: string | Date, timeZone?: string): string {
  const currentDay = zonedStartOfDayMs(new Date(), timeZone);
  const targetDay = zonedStartOfDayMs(value, timeZone);
  const diffDays = Math.floor((currentDay - targetDay) / 86_400_000);

  if (getDayKey(value, timeZone) === getDayKey(new Date(), timeZone)) return '今天';
  if (diffDays >= 0 && diffDays <= 6) return '最近 7 天';
  return '更早';
}
