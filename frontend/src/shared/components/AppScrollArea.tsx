import type { ReactNode } from 'react';
import SimpleBar, { type Props as SimpleBarProps } from 'simplebar-react';

type AppScrollAreaProps = SimpleBarProps & {
  children: ReactNode;
};

export default function AppScrollArea({
  children,
  className,
  autoHide = true,
  ...props
}: AppScrollAreaProps): JSX.Element {
  const classes = ['app-scroll-area', className].filter(Boolean).join(' ');

  return (
    <SimpleBar {...props} autoHide={autoHide} className={classes}>
      {children}
    </SimpleBar>
  );
}
