"""Driver de carga executável sem dependência nova (Sprint 28).

O cenário de referência é o `quality/carga/cockpit_drill.js` (k6), que é o que roda no
pipeline. Este script existe porque **evidência não se promete**: onde o k6 não está
instalado, a sprint ainda precisa de um número medido, e ele reproduz a mesma jornada
com o que já vem no ambiente — 50 usuários simultâneos abrindo cockpit, drill de
receita/despesa, limites e ranking.

Diferenças honestas em relação ao k6, que o relatório declara:

* usa *threads*, não corrotinas — o próprio gerador consome CPU da máquina, então o
  número medido é **conservador** (pior que a realidade), nunca otimista;
* mede o tempo de resposta HTTP completo, inclusive transporte local;
* não substitui um ensaio no ambiente alvo, com rede e concorrência de verdade.

Uso::

    python -m scripts.carga_local --usuarios 50 --duracao 60 \\
        --email admin@municipio.gov.br --senha ... --ente 2304400 --periodo 2024-B6
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

#: O critério da sprint. Rota de página tem de responder abaixo disto no P95.
ORCAMENTO_MS = 800.0


@dataclass
class Amostras:
    """Coleta por rota. O ``lock`` protege listas que várias threads alimentam."""

    dados: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    status: dict[str, dict[int, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def registrar(self, rota: str, ms: float, codigo: int) -> None:
        with self._lock:
            self.dados[rota].append(ms)
            self.status[rota][codigo] += 1


def percentil(valores: list[float], p: float) -> float:
    """Nearest rank — mesmo critério do middleware da Sprint 27, para os números
    conversarem entre si."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    import math

    posicao = max(0, math.ceil(len(ordenados) * p) - 1)
    return ordenados[posicao]


def jornada(
    cliente: httpx.Client, base: str, ente: str, periodo: str, uf: str
) -> list[tuple[str, str]]:
    """As rotas que um gestor abre de fato, na ordem em que abre."""
    return [
        ("pagina", f"/entes/{ente}/cockpit?{urlencode({'periodo': periodo})}"),
        ("pagina", f"/alertas?{urlencode({'escopo': 'ente', 'ente': ente})}"),
        ("pagina", f"/entes/{ente}/receita?{urlencode({'periodo': periodo})}"),
        ("drill", f"/entes/{ente}/receita/arvore?{urlencode({'periodo': periodo})}"),
        ("pagina", f"/entes/{ente}/despesa?{urlencode({'periodo': periodo})}"),
        (
            "drill",
            f"/entes/{ente}/despesa/arvore?{urlencode({'periodo': periodo, 'eixo': 'funcao'})}",
        ),
        ("pagina", f"/entes/{ente}/limites?{urlencode({'periodo': periodo})}"),
        ("pagina", f"/uf/{uf}/ranking?{urlencode({'indicador': 'rcl', 'periodo': periodo})}"),
    ]


def trabalhador(
    parar: threading.Event, base: str, token: str, amostras: Amostras, args
) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base, headers=headers, timeout=30.0) as cliente:
        while not parar.is_set():
            for classe, caminho in jornada(cliente, base, args.ente, args.periodo, args.uf):
                if parar.is_set():
                    break
                t0 = time.perf_counter()
                try:
                    r = cliente.get(caminho)
                    codigo = r.status_code
                except httpx.HTTPError:
                    codigo = 0
                ms = (time.perf_counter() - t0) * 1000
                rota = caminho.split("?")[0]
                # Agrupa por template: o IBGE concreto não pertence à métrica.
                rota = rota.replace(args.ente, "{ibge}").replace(f"/uf/{args.uf}", "/uf/{uf}")
                amostras.registrar(f"{classe}|{rota}", ms, codigo)
            time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--usuarios", type=int, default=50)
    parser.add_argument("--duracao", type=int, default=60, help="segundos no patamar")
    parser.add_argument("--email", required=True)
    parser.add_argument("--senha", required=True)
    parser.add_argument("--ente", default="2304400")
    parser.add_argument("--periodo", default="2024-B6")
    parser.add_argument("--uf", default="23")
    parser.add_argument("--saida", default=None)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=30.0) as cliente:
        r = cliente.post(
            "/auth/login", data={"username": args.email, "password": args.senha}
        )
        if r.status_code != 200:
            print(f"[carga] login falhou ({r.status_code}) — sem token não há teste.",
                  file=sys.stderr)
            return 2
        token = r.json()["access_token"]

    amostras = Amostras()
    parar = threading.Event()
    threads = [
        threading.Thread(
            target=trabalhador, args=(parar, args.base_url, token, amostras, args), daemon=True
        )
        for _ in range(args.usuarios)
    ]
    print(f"[carga] {args.usuarios} usuários por {args.duracao}s contra {args.base_url}")
    inicio = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(args.duracao)
    parar.set()
    for t in threads:
        t.join(timeout=35)
    decorrido = time.perf_counter() - inicio

    linhas = [
        "# Teste de carga — cockpit + drill (Sprint 28)",
        "",
        f"Executado em {datetime.now(UTC).astimezone().strftime('%d/%m/%Y %H:%M')} · "
        f"{args.usuarios} usuários simultâneos · {decorrido:.0f}s · alvo `{args.base_url}`.",
        "",
        "Cenário idêntico ao `quality/carga/cockpit_drill.js` (k6), executado por driver "
        "local em *threads*: o gerador divide CPU com a API, então os tempos abaixo são "
        "**conservadores** — o ambiente alvo tende a ser melhor, nunca pior.",
        "",
        f"Critério: **P95 < {ORCAMENTO_MS:.0f} ms** nas rotas de página.",
        "",
        "| Classe | Rota | Amostras | P50 ms | P95 ms | Máx ms | Status |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    violacoes: list[str] = []
    total_amostras = 0
    for chave in sorted(amostras.dados):
        classe, rota = chave.split("|", 1)
        vals = amostras.dados[chave]
        total_amostras += len(vals)
        p50, p95 = percentil(vals, 0.50), percentil(vals, 0.95)
        codigos = amostras.status[chave]
        resumo = ", ".join(f"{c}×{n}" for c, n in sorted(codigos.items()))
        linhas.append(
            f"| {classe} | `{rota}` | {len(vals)} | {p50:.0f} | {p95:.0f} | "
            f"{max(vals):.0f} | {resumo} |"
        )
        if p95 >= ORCAMENTO_MS:
            violacoes.append(f"{rota}: P95 {p95:.0f} ms")
        if any(c != 200 for c in codigos):
            violacoes.append(f"{rota}: respostas fora de 200 ({resumo})")

    todos = [v for vals in amostras.dados.values() for v in vals]
    linhas += [
        "",
        f"**Agregado:** {total_amostras} requisições · "
        f"P50 {percentil(todos, 0.50):.0f} ms · P95 {percentil(todos, 0.95):.0f} ms · "
        f"média {statistics.mean(todos):.0f} ms · vazão "
        f"{total_amostras / max(decorrido, 1):.1f} req/s.",
        "",
    ]
    if violacoes:
        linhas += ["## Fora do orçamento", ""] + [f"- {v}" for v in violacoes] + [""]
    else:
        linhas += ["Nenhuma rota fora do orçamento.", ""]

    conteudo = "\n".join(linhas)
    if args.saida:
        Path(args.saida).write_text(conteudo, encoding="utf-8")
        print(f"[carga] relatório em {args.saida}")
    else:
        print(conteudo)
    if violacoes:
        print(f"[carga] {len(violacoes)} violação(ões) do orçamento.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
