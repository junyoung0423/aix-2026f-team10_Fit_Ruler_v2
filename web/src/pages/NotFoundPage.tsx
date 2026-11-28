import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <section className="page">
      <header className="page-head">
        <h1>페이지를 찾을 수 없어요</h1>
      </header>
      <p>
        <Link to="/" className="text-link">
          처음 화면으로 가기
        </Link>
      </p>
    </section>
  );
}
