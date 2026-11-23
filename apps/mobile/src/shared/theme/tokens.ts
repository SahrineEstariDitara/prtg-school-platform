/**
 * Design tokens kontras tinggi untuk operasional lapangan PRTG School Platform.
 * Seluruh status memakai kombinasi warna + teks + ikon (FR-008, tidak pernah warna saja).
 */

export const colors = {
  // Surfaces & text
  bg: '#F4F6F9',
  surface: '#FFFFFF',
  surfaceAlt: '#EEF2F7',
  border: '#D5DDE8',
  borderStrong: '#B5C2D4',
  textPrimary: '#0F172A',
  textSecondary: '#475569',
  textMuted: '#64748B',
  textInverse: '#FFFFFF',

  // Brand / primary
  primary: '#1D4ED8',
  primaryDark: '#1E3A8A',
  primarySoft: '#DBEAFE',

  // Semantic statuses (high contrast)
  onlineBg: '#DCFCE7',
  onlineText: '#166534',
  onlineBorder: '#86EFAC',

  offlineBg: '#FEE2E2',
  offlineText: '#991B1B',
  offlineBorder: '#FCA5A5',

  warningBg: '#FEF3C7',
  warningText: '#92400E',
  warningBorder: '#FCD34D',

  unknownBg: '#E2E8F0',
  unknownText: '#334155',
  unknownBorder: '#CBD5E1',

  infoBg: '#E0F2FE',
  infoText: '#075985',
  infoBorder: '#7DD3FC',

  purpleBg: '#F3E8FF',
  purpleText: '#6B21A8',
  purpleBorder: '#D8B4FE',

  danger: '#DC2626',
  success: '#15803D',
  amber: '#B45309',
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radius = {
  sm: 6,
  md: 10,
  lg: 14,
  pill: 999,
} as const;

export const typography = {
  h1: { fontSize: 22, fontWeight: '700' as const, lineHeight: 28 },
  h2: { fontSize: 18, fontWeight: '700' as const, lineHeight: 24 },
  h3: { fontSize: 15, fontWeight: '700' as const, lineHeight: 20 },
  body: { fontSize: 14, fontWeight: '400' as const, lineHeight: 20 },
  bodyBold: { fontSize: 14, fontWeight: '600' as const, lineHeight: 20 },
  caption: { fontSize: 12, fontWeight: '400' as const, lineHeight: 16 },
  captionBold: { fontSize: 12, fontWeight: '600' as const, lineHeight: 16 },
  badge: { fontSize: 11, fontWeight: '700' as const, lineHeight: 14 },
} as const;
