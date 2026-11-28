import { useEffect } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router-dom';
import { StepIndicator } from './components/StepIndicator';
import { GarmentSelectPage } from './pages/GarmentSelectPage';
import { InputPage } from './pages/InputPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { ResultPage } from './pages/ResultPage';

/** 화면을 옮기면 맨 위부터 보이게 한다(티셔츠 목록 아래에서 누르면 결과 화면이 중간부터 보이던 문제). */
function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export function App() {
  return (
    <div className="app">
      <ScrollToTop />
      <header className="app-header">
        <div className="app-header-inner">
          <Link to="/" className="brand">
            <span className="brand-mark" aria-hidden="true" />
            Fit Ruler
          </Link>
          <StepIndicator />
        </div>
      </header>
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
