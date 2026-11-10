import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import './index.css'

const base = import.meta.env.BASE_URL.replace(/\/$/, '')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={base || undefined}>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
