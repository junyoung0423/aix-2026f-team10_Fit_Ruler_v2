import { Link, Route, Routes } from 'react-router-dom';
import { USE_MOCK } from './api/client';
import { StepIndicator } from './components/StepIndicator';
import { GarmentSelectPage } from './pages/GarmentSelectPage';
import { InputPage } from './pages/InputPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { ResultPage } from './pages/ResultPage';

export function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="brand">
          Fit Ruler
        </Link>
        <StepIndicator />
      </header>
      {USE_MOCK && (
        <p className="mock-banner" role="note">
          가짜 API로 동작 중입니다. 티셔츠와 결과는 화면 개발용 예시입니다.
        </p>
      )}
      <main className="app-main">
        <Routes>
          <Route path="/" element={<InputPage />} />
          <Route path="/garments" element={<GarmentSelectPage />} />
          <Route path="/fittings/:jobId" element={<ResultPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  );
}
