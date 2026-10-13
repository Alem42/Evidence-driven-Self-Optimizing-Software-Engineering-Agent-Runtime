import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { App } from './app/App';
import './styles/app.css';
import './styles/bench.css';
import './styles/workbench.css';

const client = new QueryClient({
  defaultOptions: { queries: { staleTime: 800, retry: 1, refetchOnWindowFocus: true } },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
