# husky-cidades

Gera as ruas de cada um dos 5.570 municípios brasileiros para a animação do site da Husky,
que mostra o despacho rodando na cidade de quem visita.

- `gerar_cidades.py`: lê as ruas do OpenStreetMap e grava um arquivo pequeno por município
  (ruas num raio de 2,6 km do centro; 3,4 km nas capitais).
- `.github/workflows/gerar-cidades.yml`: o robô. Baixa o mapa do Brasil, roda o script e publica
  os arquivos no projeto `husky-cidades` do Cloudflare Pages.

## Como rodar

Aba **Actions** → **Gerar ruas das cidades** → **Run workflow**. Leva cerca de uma hora.
Vale repetir uma vez por ano, para pegar ruas novas.

Precisa de dois segredos em **Settings → Secrets and variables → Actions**:
`CLOUDFLARE_API_TOKEN` e `CLOUDFLARE_ACCOUNT_ID`.

## Fontes e licenças

- Ruas: © colaboradores do OpenStreetMap, licença ODbL (https://www.openstreetmap.org/copyright).
- Municípios e coordenadas: IBGE, via https://github.com/kelvins/municipios-brasileiros (MIT).
