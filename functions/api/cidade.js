// Devolve a cidade aproximada de quem visita, pelo IP, com a geolocalização do próprio Cloudflare.
// Nada é guardado. Só responde a páginas da Husky.
const PERMITIDOS = [
  /^https:\/\/([a-z0-9-]+\.)*gohusky\.net$/,
  /^https:\/\/([a-z0-9-]+\.)*[a-z0-9-]*husky[a-z0-9-]*\.([a-z0-9-]+\.)?(pages|workers)\.dev$/,
  /^http:\/\/localhost(:\d+)?$/,
];

export function onRequestGet({ request }) {
  const origem = request.headers.get('Origin') || '';
  const cf = request.cf || {};
  const corpo = JSON.stringify({
    cidade: cf.city || null,
    uf: cf.regionCode || null,
    pais: cf.country || null,
    lat: cf.latitude != null ? Number(cf.latitude) : null,
    lon: cf.longitude != null ? Number(cf.longitude) : null,
  });
  const cabecalhos = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store', vary: 'Origin' };
  if (PERMITIDOS.some((regra) => regra.test(origem))) cabecalhos['access-control-allow-origin'] = origem;
  return new Response(corpo, { headers: cabecalhos });
}
