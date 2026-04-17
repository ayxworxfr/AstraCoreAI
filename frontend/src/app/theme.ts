import { theme } from 'antd';
import type { ThemeConfig } from 'antd';

const baseToken = {
  colorPrimary: '#1677ff',
  borderRadius: 8,
  fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif",
};

export const lightTheme: ThemeConfig = {
  token: {
    ...baseToken,
    colorBgBase: '#f5f7fa',
    colorBgContainer: '#ffffff',
    colorBorderSecondary: '#e8edf2',
  },
  components: {
    Layout: {
      headerBg: '#ffffff',
      siderBg: '#f5f7fa',
    },
  },
};

export const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    ...baseToken,
    colorBgBase: '#0d1117',
    colorBgContainer: '#161b22',
    colorBorderSecondary: '#30363d',
  },
};
