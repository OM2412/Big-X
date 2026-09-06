export default function Custom404() {
  return (
    <div style={{ textAlign: 'center', padding: '100px 20px', fontFamily: 'sans-serif', color: '#fff' }}>
      <h1>404 — Page Not Found</h1>
      <p style={{ color: '#959eb1' }}>The page you are looking for does not exist.</p>
      <a href="/" style={{ color: '#7890ff', textDecoration: 'none', fontWeight: 'bold' }}>
        Return to Home →
      </a>
    </div>
  );
}
