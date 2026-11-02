export type BjorkTone = 'dark' | 'light';

export interface BjorkTokens {
  accent: string;
  surface: string;
  surfaceActive: string;
  border: string;
  text: string;
  textMedium: string;
  textSoft: string;
}

/** Host surface tokens the beam reads its defaults from, per tone. */
export const BJORK_PALETTE: Record<BjorkTone, BjorkTokens> = {
  dark: {
    accent: '#ec5c13',
    surface: '#121212',
    surfaceActive: '#202020',
    border: '#232323',
    text: '#ededed',
    textMedium: 'rgba(237,237,237,0.72)',
    textSoft: 'rgba(237,237,237,0.5)',
  },
  light: {
    accent: '#dc4a0c',
    surface: '#faf7f2',
    surfaceActive: '#f0eae1',
    border: '#e2dcd2',
    text: '#1a1a1a',
    textMedium: 'rgba(26,26,26,0.72)',
    textSoft: 'rgba(26,26,26,0.5)',
  },
};
