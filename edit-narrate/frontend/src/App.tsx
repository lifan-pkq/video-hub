import { Navigate, Route, Routes } from 'react-router-dom'
import { Shell } from './components/Shell.tsx'
import EditPage from './pages/EditPage.tsx'
import NarratePage from './pages/NarratePage.tsx'

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/edit" replace />} />
        <Route path="/edit" element={<EditPage />} />
        <Route path="/narrate" element={<NarratePage />} />
      </Route>
    </Routes>
  )
}
