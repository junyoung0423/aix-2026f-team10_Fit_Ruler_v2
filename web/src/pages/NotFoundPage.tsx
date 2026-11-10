import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <section>
      <h1>페이지를 찾을 수 없습니다</h1>
      <p>
        <Link to="/">처음 화면으로 가기</Link>
      </p>
    </section>
  );
}
