// The static fixture harness remains available; product /console always uses HTTP.
if (location.pathname.startsWith('/console/')) await import('./mission-app.mjs');
else await import('./fixture-app.mjs');
