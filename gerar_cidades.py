#!/usr/bin/env python3
"""Gera, para cada município brasileiro, um arquivo pequeno com as ruas em volta do centro.

Entrada: as vias do OpenStreetMap (arquivo .osm.pbf ou .osm) e a lista de municípios do IBGE
com coordenadas. Saída, na pasta indicada:
  c/<codigo_ibge>.json  ruas do município (coordenadas em metros a partir do centro) e,
                        quando houver, lagos, represas e rios largos ("w")
  indice.json           lista de municípios, usada para achar a cidade e para a busca
  _headers              cabeçalhos do Cloudflare Pages (CORS e cache)

Dados de ruas: © colaboradores do OpenStreetMap, licença ODbL.
"""

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict

import osmium

# 0 = vias principais, 1 = coletoras, 2 = locais
CLASSES = {
    'motorway': 0, 'trunk': 0, 'primary': 0, 'motorway_link': 0, 'trunk_link': 0, 'primary_link': 0,
    'secondary': 1, 'secondary_link': 1, 'tertiary': 1, 'tertiary_link': 1,
    'unclassified': 2, 'residential': 2, 'living_street': 2, 'road': 2,
}
RAIO_PADRAO = 2600      # metros a partir do centro do município
RAIO_CAPITAL = 3400
MAX_NOS = 12000         # acima disso, o raio encolhe (capitais muito densas)
MIN_NOS = 120           # abaixo disso, o site usa a cidade gerada
GRADE = 0.05            # graus por célula da grade de busca (~5,5 km)


def log(*partes):
    print(time.strftime('%H:%M:%S'), *partes, flush=True)


def ler_municipios(caminho_municipios, caminho_estados):
    with open(caminho_estados, encoding='utf-8-sig') as f:
        ufs = {linha['codigo_uf']: linha['uf'] for linha in csv.DictReader(f)}
    cidades = []
    with open(caminho_municipios, encoding='utf-8-sig') as f:
        for linha in csv.DictReader(f):
            lat = float(linha['latitude'])
            lon = float(linha['longitude'])
            raio = RAIO_CAPITAL if linha['capital'] == '1' else RAIO_PADRAO
            margem = raio + 150
            cidades.append({
                'ibge': int(linha['codigo_ibge']),
                'nome': linha['nome'],
                'uf': ufs.get(linha['codigo_uf'], ''),
                'lat': lat,
                'lon': lon,
                'raio': raio,
                'r2': margem * margem,
                'my': 111_320.0,
                'mx': 111_320.0 * math.cos(math.radians(lat)),
                'nos': {},
                'arestas': [],
                'agua': [],
            })
    return cidades


def montar_grade(cidades):
    grade = defaultdict(list)
    for k, c in enumerate(cidades):
        grade[(math.floor(c['lat'] / GRADE), math.floor(c['lon'] / GRADE))].append(k)
    return grade


class Leitor(osmium.SimpleHandler):
    """Passa por todas as vias e entrega cada trecho às cidades cujo centro está perto dele."""

    def __init__(self, cidades, grade):
        super().__init__()
        self.cidades = cidades
        self.grade = grade
        # Células com algum município por perto: o resto do país é descartado com uma consulta só
        self.perto = {(gi + di, gj + dj) for (gi, gj) in grade for di in (-1, 0, 1) for dj in (-1, 0, 1)}
        self.vias = 0
        self.uteis = 0

    def way(self, w):
        classe = CLASSES.get(w.tags.get('highway'))
        if classe is None or w.tags.get('area') == 'yes':
            return
        self.vias += 1
        if self.vias % 500_000 == 0:
            log(f'{self.vias:,} vias lidas, {self.uteis:,} perto de algum município')
        pontos = []
        try:
            for n in w.nodes:
                loc = n.location
                if loc.valid():
                    pontos.append((n.ref, loc.lat, loc.lon))
        except osmium.InvalidLocationError:
            return
        if len(pontos) < 2:
            return
        usada = False
        for (ra, lata, lona), (rb, latb, lonb) in zip(pontos, pontos[1:]):
            latm = (lata + latb) * 0.5
            lonm = (lona + lonb) * 0.5
            gi = math.floor(latm / GRADE)
            gj = math.floor(lonm / GRADE)
            if (gi, gj) not in self.perto:
                continue
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for k in self.grade.get((gi + di, gj + dj), ()):
                        c = self.cidades[k]
                        dy = (latm - c['lat']) * c['my']
                        dx = (lonm - c['lon']) * c['mx']
                        if dx * dx + dy * dy > c['r2']:
                            continue
                        nos = c['nos']
                        if ra not in nos:
                            nos[ra] = ((lona - c['lon']) * c['mx'], -(lata - c['lat']) * c['my'])
                        if rb not in nos:
                            nos[rb] = ((lonb - c['lon']) * c['mx'], -(latb - c['lat']) * c['my'])
                        c['arestas'].append((ra, rb, classe))
                        usada = True
        if usada:
            self.uteis += 1


TIPOS_AGUA = {('natural', 'water'), ('waterway', 'riverbank'), ('landuse', 'reservoir')}
PASSO_AGUA = 0.00012   # graus (~13 m): pontos mais próximos que isso são descartados
AREA_MINIMA = 3000     # m²: lagoas menores não aparecem


def decimar(pontos, passo=PASSO_AGUA):
    """Simplificação rápida: mantém um ponto só quando ele se afasta o bastante do último mantido."""
    if len(pontos) < 4:
        return pontos
    saida = [pontos[0]]
    for p in pontos[1:-1]:
        q = saida[-1]
        if abs(p[0] - q[0]) > passo or abs(p[1] - q[1]) > passo:
            saida.append(p)
    saida.append(pontos[-1])
    return saida


def recortar(anel, q):
    """Sutherland-Hodgman: recorta um polígono no quadrado [-q, q] x [-q, q]."""
    def passada(pts, dentro, cruzar):
        saida = []
        if not pts:
            return saida
        anterior = pts[-1]
        for atual in pts:
            if dentro(atual):
                if not dentro(anterior):
                    saida.append(cruzar(anterior, atual))
                saida.append(atual)
            elif dentro(anterior):
                saida.append(cruzar(anterior, atual))
            anterior = atual
        return saida

    def em_x(limite):
        return lambda a, b: (limite, a[1] + (b[1] - a[1]) * (limite - a[0]) / ((b[0] - a[0]) or 1e-9))

    def em_z(limite):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (limite - a[1]) / ((b[1] - a[1]) or 1e-9), limite)

    pts = passada(anel, lambda p: p[0] >= -q, em_x(-q))
    pts = passada(pts, lambda p: p[0] <= q, em_x(q))
    pts = passada(pts, lambda p: p[1] >= -q, em_z(-q))
    pts = passada(pts, lambda p: p[1] <= q, em_z(q))
    return pts


def area_poligono(pts):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


class LeitorAgua(osmium.SimpleHandler):
    """Lagos, represas e rios largos: cada área é entregue, já recortada, aos municípios que ela toca."""

    def __init__(self, cidades, grade):
        super().__init__()
        self.cidades = cidades
        self.grade = grade
        self.areas = 0
        self.usadas = 0

    def area(self, a):
        if not any((k, a.tags.get(k)) in TIPOS_AGUA for k in ('natural', 'waterway', 'landuse')):
            return
        try:
            aneis = []
            for externo in a.outer_rings():
                pts = [(n.location.lat, n.location.lon) for n in externo]
                internos = [[(n.location.lat, n.location.lon) for n in interno] for interno in a.inner_rings(externo)]
                aneis.append((pts, internos))
        except osmium.InvalidLocationError:
            return
        self.areas += 1
        if self.areas % 100_000 == 0:
            log(f'{self.areas:,} áreas de água lidas, {self.usadas:,} perto de algum município')
        for pts, internos in aneis:
            if len(pts) < 4:
                continue
            lats = [p[0] for p in pts]
            lons = [p[1] for p in pts]
            margem = 0.045
            candidatas = set()
            for gi in range(math.floor((min(lats) - margem) / GRADE), math.floor((max(lats) + margem) / GRADE) + 1):
                for gj in range(math.floor((min(lons) - margem) / GRADE), math.floor((max(lons) + margem) / GRADE) + 1):
                    candidatas.update(self.grade.get((gi, gj), ()))
            if not candidatas:
                continue
            externo = decimar(pts)
            internos = [decimar(i) for i in internos if len(i) >= 4]
            for k in candidatas:
                c = self.cidades[k]
                q = RAIO_CAPITAL + 300
                if (max(lats) - c['lat']) * c['my'] < -q or (min(lats) - c['lat']) * c['my'] > q:
                    continue
                if (max(lons) - c['lon']) * c['mx'] < -q or (min(lons) - c['lon']) * c['mx'] > q:
                    continue
                para_metros = lambda anel: [((lon - c['lon']) * c['mx'], -(lat - c['lat']) * c['my']) for lat, lon in anel]
                fora = recortar(para_metros(externo), q)
                if len(fora) < 3 or area_poligono(fora) < AREA_MINIMA:
                    continue
                buracos = []
                for interno in internos:
                    b = recortar(para_metros(interno), q)
                    if len(b) >= 3 and area_poligono(b) >= AREA_MINIMA / 3:
                        buracos.append(b)
                c['agua'].append([fora] + buracos)
                self.usadas += 1


def maior_componente(nos, arestas):
    vizinhos = defaultdict(list)
    for a, b, _ in arestas:
        vizinhos[a].append(b)
        vizinhos[b].append(a)
    visto = set()
    melhor = set()
    for inicio in vizinhos:
        if inicio in visto:
            continue
        grupo = {inicio}
        pilha = [inicio]
        visto.add(inicio)
        while pilha:
            u = pilha.pop()
            for v in vizinhos[u]:
                if v not in visto:
                    visto.add(v)
                    grupo.add(v)
                    pilha.append(v)
        if len(grupo) > len(melhor):
            melhor = grupo
    return melhor


def simplificar(nos, arestas):
    """Remove nós intermediários quase retos (mantém cruzamentos e curvas)."""
    adj = defaultdict(dict)
    for a, b, k in arestas:
        if a == b:
            continue
        adj[a][b] = min(k, adj[a].get(b, 9))
        adj[b][a] = min(k, adj[b].get(a, 9))
    mudou = True
    while mudou:
        mudou = False
        for n in list(adj.keys()):
            viz = adj.get(n)
            if not viz or len(viz) != 2:
                continue
            (a, ka), (b, kb) = viz.items()
            if ka != kb or b in adj[a]:
                continue
            ax, az = nos[a]
            nx, nz = nos[n]
            bx, bz = nos[b]
            v1 = (nx - ax, nz - az)
            v2 = (bx - nx, bz - nz)
            l1 = math.hypot(*v1)
            l2 = math.hypot(*v2)
            if l1 == 0 or l2 == 0:
                continue
            cosseno = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
            if cosseno < 0.995 or l1 + l2 > 220:
                continue
            del adj[a][n], adj[b][n], adj[n]
            adj[a][b] = ka
            adj[b][a] = ka
            mudou = True
    vistas = set()
    resultado = []
    for a, viz in adj.items():
        for b, k in viz.items():
            chave = (a, b) if a < b else (b, a)
            if chave not in vistas:
                vistas.add(chave)
                resultado.append((a, b, k))
    return resultado


def gravar_cidade(c, pasta):
    arestas = c['arestas']
    nos = c['nos']
    if not arestas:
        return 0
    raio = c['raio']
    # Capitais muito densas: encolhe o raio até caber no limite de nós
    for _ in range(4):
        manter = maior_componente(nos, arestas)
        if len(manter) <= MAX_NOS:
            break
        raio *= math.sqrt(MAX_NOS / len(manter)) * 0.97
        r2 = raio * raio
        arestas = [(a, b, k) for a, b, k in arestas
                   if (nos[a][0] ** 2 + nos[a][1] ** 2) <= r2 and (nos[b][0] ** 2 + nos[b][1] ** 2) <= r2]
    manter = maior_componente(nos, arestas)
    arestas = [(a, b, k) for a, b, k in arestas if a in manter and b in manter]
    arestas = simplificar(nos, arestas)
    usados = {}
    lista_nos = []
    lista_arestas = []
    classes = []
    for a, b, k in arestas:
        for n in (a, b):
            if n not in usados:
                usados[n] = len(usados)
                x, z = nos[n]
                lista_nos += [round(x), round(z)]
        lista_arestas += [usados[a], usados[b]]
        classes.append(str(k))
    if len(usados) < MIN_NOS:
        return 0
    agua = []
    q = raio + 250
    for poligono in c.get('agua', []):
        fora = recortar(poligono[0], q)
        if len(fora) < 3 or area_poligono(fora) < AREA_MINIMA:
            continue
        aneis = [fora] + [b for b in (recortar(i, q) for i in poligono[1:]) if len(b) >= 3]
        agua.append([[v for p in anel for v in (round(p[0]), round(p[1]))] for anel in aneis])
    dados = {
        'v': 1,
        'ibge': c['ibge'],
        'nome': c['nome'],
        'uf': c['uf'],
        'raio': round(raio),
        'n': lista_nos,
        'e': lista_arestas,
        'k': ''.join(classes),
    }
    if agua:
        dados['w'] = agua
    with open(os.path.join(pasta, 'c', f"{c['ibge']}.json"), 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, separators=(',', ':'))
    return len(usados)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vias', required=True)
    p.add_argument('--agua', help='lagos, represas e rios largos (osm.pbf); opcional')
    p.add_argument('--municipios', required=True)
    p.add_argument('--estados', required=True)
    p.add_argument('--saida', default='saida')
    p.add_argument('--minimo', type=float, default=0.5, help='fração mínima de municípios com ruas para considerar sucesso')
    args = p.parse_args()

    os.makedirs(os.path.join(args.saida, 'c'), exist_ok=True)
    cidades = ler_municipios(args.municipios, args.estados)
    log(f'{len(cidades)} municípios')
    leitor = Leitor(cidades, montar_grade(cidades))
    leitor.apply_file(args.vias, locations=True, idx='flex_mem')
    log(f'{leitor.vias:,} vias lidas no total, {leitor.uteis:,} perto de algum município')
    if args.agua:
        leitor_agua = LeitorAgua(cidades, montar_grade(cidades))
        leitor_agua.apply_file(args.agua, locations=True, idx='flex_mem')
        log(f'{leitor_agua.areas:,} áreas de água lidas, {leitor_agua.usadas:,} trechos entregues a municípios')

    indice = []
    com_ruas = 0
    for k, c in enumerate(cidades):
        qtd = gravar_cidade(c, args.saida)
        if qtd:
            com_ruas += 1
        indice.append([c['ibge'], c['nome'], c['uf'], round(c['lat'], 4), round(c['lon'], 4), qtd])
        c['nos'] = {}
        c['arestas'] = []
        c['agua'] = []
        if (k + 1) % 500 == 0:
            log(f'{k + 1} municípios gravados')
    indice.sort(key=lambda m: m[1])
    with open(os.path.join(args.saida, 'indice.json'), 'w', encoding='utf-8') as f:
        json.dump({'v': 1, 'fonte': '© colaboradores do OpenStreetMap (ODbL)', 'municipios': indice}, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(args.saida, '_headers'), 'w', encoding='utf-8') as f:
        f.write('/*\n  Access-Control-Allow-Origin: *\n  Cache-Control: public, max-age=604800\n')
    com_agua = sum(1 for m in indice if m[5] and os.path.exists(os.path.join(args.saida, 'c', f'{m[0]}.json')) and '"w":' in open(os.path.join(args.saida, 'c', f'{m[0]}.json'), encoding='utf-8').read())
    log(f'Pronto: {com_ruas} de {len(cidades)} municípios com ruas suficientes; {com_agua} com água')
    if com_ruas < len(cidades) * args.minimo:
        log('Atenção: menos da metade dos municípios teve ruas. Confira o arquivo de vias.')
        sys.exit(1)


if __name__ == '__main__':
    main()
