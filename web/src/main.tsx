import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { FittingProvider } from './state/FittingContext';
import './styles.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('#root 요소가 없습니다.');

createRoot(rootElement).render(
  <StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <FittingProvider>
        <App />
      </FittingProvider>
    </BrowserRouter>
  </StrictMode>,
);
