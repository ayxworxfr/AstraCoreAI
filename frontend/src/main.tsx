import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './app/App';
import { useSettingsStore } from './stores/settingsStore';
import { lightTheme, darkTheme } from './app/theme';
import './global.css';

function Root(): JSX.Element {
  const theme = useSettingsStore((s) => s.theme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  return (
    <ConfigProvider locale={zhCN} theme={theme === 'dark' ? darkTheme : lightTheme}>
      <App />
    </ConfigProvider>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
