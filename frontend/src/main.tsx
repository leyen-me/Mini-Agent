import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Root } from './Root';
import 'katex/dist/katex.min.css';
import './styles.css';

const container = document.getElementById('root');
if (!container) throw new Error('Root element not found');
createRoot(container).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
