#!/usr/bin/env python3
"""
Simulações do Plano B (critérios de aceite das Partes A–D do plano-fallback-estacao).
Roda SEM segredos: dados da estação são sintéticos; só o Open-Meteo é chamado de verdade.

Cenários: 1) dia OK  2) ontem em fallback  3) buraco antigo na janela de 15d
          4) retorno da estação (= cenário 1 de novo)  5) falha dupla (Open-Meteo fora)

Uso: python teste_fallback.py   → imprime o resultado de cada cenário e grava
     saida/teste_fallback_N.html para inspeção visual dos avisos.
"""
import datetime, pathlib, sys

import gerar_painel as gp
import fallback_estacao

RAIZ = pathlib.Path(__file__).parent
HOJE = (datetime.datetime.utcnow() - datetime.timedelta(hours=3)).date()

IA_STUB = {"risco_nivel": "OK", "risco_frase": "Teste do Plano B — texto simulado.",
           "culturas": [{"cultura": c, "nivel": "verde", "acompanhar": "teste", "doencas": "teste",
                         "pragas": "teste", "nutricao": "teste"} for c in ["banana", "uva", "citros", "cacau"]]}


def dados_sinteticos(excluir=()):
    """30 dias terminando ontem (cobre a janela inteira do fallback), no formato
    de ler_clima(). `excluir` = datas removidas."""
    out = []
    for i in range(30, 0, -1):
        d = HOJE - datetime.timedelta(days=i)
        if d in excluir:
            continue
        out.append({"data": d, "et0": 4.0, "vpd": 0.9, "tmax": 30.0, "tmin": 15.0, "ur": 65.0,
                    "rad": 18.0, "vento": 1.5, "chuva": 0.0, "molh": 3.0, "status": "OK"})
    return out


def rodar(nome, excluir=(), quebrar_openmeteo=False):
    print(f"\n=== {nome} ===")
    dados = dados_sinteticos(excluir)
    if quebrar_openmeteo:
        original = fallback_estacao._buscar
        fallback_estacao._buscar = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulação: Open-Meteo fora"))
    try:
        dados, fb = gp.aplicar_fallback(dados, HOJE)
    finally:
        if quebrar_openmeteo:
            fallback_estacao._buscar = original
    ag = gp.agregar(dados)
    ag["fb"] = fb
    o = ag["ontem"]
    print(f"  ontem no painel: {o['data']} | fonte: {'ESTIMADO' if o.get('estimado') else 'estação'} | status: {o['status']}")
    print(f"  fb: datas_estimadas={[d.strftime('%d/%m') for d in fb['datas']]} | sem_dado={[d.strftime('%d/%m') for d in fb['sem_dado']]} | consecutivos={fb['consec']} | molh_est={fb['molh_est_h']}h")
    html = gp.montar_html(ag, [], IA_STUB, [], {"sassaki": ""})
    marcas = {"faixa_amarela": "Estação INMET A545 indisponível" in html or "Dia(s) com estimativa" in html,
              "faixa_vermelha": "Sem dado em" in html,
              "etiqueta_estimado": "(estimado)" in html,
              "fonte_titulo": ("estimativa Open-Meteo" in html) if o.get("estimado") else ("estação INMET A545 (real)" in html),
              "metodologia": "Plano B (estação indisponível)" in html}
    print(f"  marcas no HTML: {marcas}")
    n = nome.split(")")[0]
    (RAIZ / "saida").mkdir(exist_ok=True)
    (RAIZ / "saida" / f"teste_fallback_{n}.html").write_text(html, encoding="utf-8")
    return fb, marcas


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ok = True

    fb, m = rodar("1) dia OK — estação completa")
    ok &= not fb["datas"] and not fb["sem_dado"] and m["fonte_titulo"] and not m["faixa_amarela"]

    fb, m = rodar("2) ontem em fallback", excluir={HOJE - datetime.timedelta(days=1)})
    ok &= fb["datas"] and fb["consec"] == 1 and m["faixa_amarela"] and m["etiqueta_estimado"] and m["fonte_titulo"]

    fb, m = rodar("3) buraco antigo (10 dias atrás)", excluir={HOJE - datetime.timedelta(days=10)})
    ok &= len(fb["datas"]) == 1 and not fb["consec"] and m["faixa_amarela"] and not m["etiqueta_estimado"]

    fb, m = rodar("4) retorno da estação (tudo real de novo)")
    ok &= not fb["datas"] and m["fonte_titulo"] and not m["faixa_amarela"]

    fb, m = rodar("5) falha dupla (estação E Open-Meteo fora)",
                  excluir={HOJE - datetime.timedelta(days=1)}, quebrar_openmeteo=True)
    ok &= fb["sem_dado"] and m["faixa_vermelha"]

    print(f"\n{'✅ TODOS os cenários passaram' if ok else '❌ ALGUM cenário falhou'}")
    sys.exit(0 if ok else 1)
