#!/usr/bin/env python3
"""
Gera o painel AgroClima Pirapora e publica na pagina WordPress 1649.
Substitui o fluxo do Make. Observado = estacao real INMET (planilha BD_clima_auto);
previsao = Open-Meteo; analise = Claude Sonnet 4.6 ancorada em base_doencas/.

Uso:
  python gerar_painel.py --dry-run     # gera saida/painel.html, nao publica
  python gerar_painel.py --publicar    # gera e publica na pagina 1649
"""
import os, sys, json, base64, glob, datetime, pathlib, urllib.parse

import requests

import fallback_estacao

SHEET_ID   = "1oaqeRuwsQ7xTpM4CWv2PqC7GlTPHRCZGD5882IveCM4"   # AgroClima (BD_clima_auto)
ABA_CLIMA  = "BD_clima_auto"
ABA_PATRO  = "patrocinadores"
WP_URL     = "https://sassakiagronegocios.com.br/wp-json/wp/v2/pages/1649"
WP_TEMPLATE = "templates/tpl-helper-min-pb.php"   # canvas limpo (sem cabeçalho/sidebar/título), mesmo da home 1786 e artigos 1795 — setado a cada publish p/ não exigir config manual
LAT, LON   = -17.35, -44.91
MODELO_IA  = "claude-sonnet-4-6"
RAIZ       = pathlib.Path(__file__).parent
DRY        = "--dry-run" in sys.argv or "--publicar" not in sys.argv

MESES = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
DIAS  = ["seg","ter","qua","qui","sex","sáb","dom"]

# Climatologia de molhamento foliar (h/dia com UR>=90%), média histórica da estação INMET A545
# (Pirapora, ~11 anos: 2013-2025). Base para "este mês está acima/abaixo do normal".
CLIM_MOLH = {1:5.3, 2:5.3, 3:4.3, 4:4.2, 5:3.5, 6:2.4, 7:1.1, 8:0.1, 9:0.3, 10:1.0, 11:3.1, 12:5.4}

# Faixas de VPD para calibrar a leitura da IA (impedir chamar VPD baixo de "elevado").
# IMPORTANTE: o VPD do painel é MÉDIA DIÁRIA do ar (estação INMET A545), não o pico de
# meio-dia; por isso as faixas abaixo são mais baixas que os limiares instantâneos
# folha-ar dos estudos. Os limiares fisiológicos citados são leaf-to-air (instantâneos),
# então uma média diária já cruzando 1,0-1,5 kPa implica picos de meio-dia estressantes.
# Referências:
#  - Grossiord, C., Buckley, T.N., Cernusak, L.A., Novick, K.A., Poulter, B.,
#    Siegwolf, R.T.W., Sperry, J.S. & McDowell, N.G. (2020). Plant responses to rising
#    vapor pressure deficit. New Phytologist, 226(6): 1550-1566. doi:10.1111/nph.16485
#    (a maioria das espécies reduz a condutância estomática à medida que o VPD sobe).
#  - Eyland, D., Gambart, C., Swennen, R. & Carpentier, S. (2023). Unravelling the
#    diversity in water usage among wild banana species in response to vapour pressure
#    deficit. Frontiers in Plant Science, 14: 1068191. doi:10.3389/fpls.2023.1068191
#    (em bananeira, resposta estomática/transpiratória significativa a partir de
#    VPDleaf >= 1,5 kPa; breakpoints entre 1,6 e 2,5 kPa).
VPD_FAIXAS_TXT = (
    "- VPD é MÉDIA DIÁRIA do ar (estação INMET A545), NÃO o pico de meio-dia. "
    "Classifique a média diária por estas faixas calibradas:\n"
    "    • < 0,4 kPa  = BAIXO (ar úmido, baixa demanda evaporativa, maior chance de molhamento foliar);\n"
    "    • 0,4-1,0 kPa = ADEQUADO (boa condutância estomática e troca gasosa na maioria das culturas);\n"
    "    • 1,0-1,5 kPa = MODERADO (demanda evaporativa crescente, regulação estomática inicial);\n"
    "    • > 1,5 kPa  = ALTO (a bananeira já mostra resposta estomática/transpiratória significativa; "
    "a maioria das espécies reduz a condutância).\n"
    "  NUNCA classifique o VPD como 'elevado/alto' abaixo de 1,0 kPa de média diária. "
    "Base: Grossiord et al. (2020), New Phytol. 226(6):1550-1566; Eyland et al. (2023), Front. Plant Sci. 14:1068191."
)

def log(m): print(f"  {m}", flush=True)

def f(v):
    try: return float(str(v).replace(",", ".")) if str(v).strip() not in ("","-","nan","None") else None
    except: return None

def br(v, c=1):
    if v is None: return "—"
    try: return f"{round(float(v), c):.{c}f}".replace(".", ",")
    except: return str(v)

# ---------- 1. CLIMA (estacao real INMET, via Supabase — somente leitura) ----------
SUPA_URL = os.environ.get("SUPABASE_URL", "https://fxkdjzguyxtbfadmoemg.supabase.co")
SUPA_KEY = os.environ.get("SUPABASE_KEY", "")

def ler_clima():
    cols = "data,et0_mm,vpd_kpa,temp_max_c,temp_min_c,umidade_relativa_pct,radiacao_solar_mj,vento_ms,precipitacao_mm,status,horas_molhamento"
    url = f"{SUPA_URL}/rest/v1/clima?select={cols}&order=data.desc&limit=400"
    r = requests.get(url, headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}"}, timeout=30)
    r.raise_for_status()
    dados = []
    for x in r.json():
        try: dt = datetime.date.fromisoformat(str(x["data"])[:10])
        except: continue
        dados.append({"data": dt, "et0": f(x.get("et0_mm")), "vpd": f(x.get("vpd_kpa")),
            "tmax": f(x.get("temp_max_c")), "tmin": f(x.get("temp_min_c")), "ur": f(x.get("umidade_relativa_pct")),
            "rad": f(x.get("radiacao_solar_mj")), "vento": f(x.get("vento_ms")), "chuva": f(x.get("precipitacao_mm")),
            "molh": f(x.get("horas_molhamento")), "status": x.get("status") or ""})
    dados.sort(key=lambda d: d["data"])
    return dados

def soma(seq): seq=[x for x in seq if x is not None]; return round(sum(seq),1) if seq else 0.0
def media(seq): seq=[x for x in seq if x is not None]; return round(sum(seq)/len(seq),2) if seq else None

# ---------- 1b. PLANO B (fallback da estação — dias faltantes viram estimativa marcada) ----------
def aplicar_fallback(dados, hoje_sp):
    """Se faltarem dias recentes na tabela clima (estação falhou), estima-os com o
    Open-Meteo (fallback_estacao.py, limiares calibrados na PARTE ZERO) e devolve
    (dados_completos, fb). Nada é gravado em banco/planilha — só nesta execução."""
    ontem = hoje_sp - datetime.timedelta(days=1)
    datas_ok = {x["data"] for x in dados}
    # 30 dias: cobre TODOS os acumulados do painel (molh 15d, ET0/chuva 30d) —
    # buraco de 16–30 dias atrás também deflacionava o 30d em silêncio.
    janela = [ontem - datetime.timedelta(days=i) for i in range(30)]
    faltantes = [d for d in janela if d not in datas_ok]
    fb = {"datas": [], "ontem_estimado": False, "sem_dado": [], "molh_est_h": 0, "consec": 0}
    if faltantes:
        log(f"Fallback: {len(faltantes)} dia(s) sem estação na janela de 15d: "
            + ", ".join(d.strftime("%d/%m") for d in sorted(faltantes)))
        estimados = fallback_estacao.estimar_dias(faltantes, hoje=hoje_sp)
        for d in sorted(faltantes):
            if d in estimados:
                dados.append(estimados[d])
                fb["datas"].append(d)
                fb["molh_est_h"] += estimados[d]["molh"] or 0
            else:
                fb["sem_dado"].append(d)   # falha dupla: nem estação, nem estimativa
        dados.sort(key=lambda x: x["data"])
        fb["ontem_estimado"] = ontem in estimados
        # dias consecutivos sem estação terminando em ontem (para o alerta de 48h)
        d = ontem
        while d in set(faltantes):
            fb["consec"] += 1; d -= datetime.timedelta(days=1)
    return dados, fb

def agregar(dados):
    if not dados: raise SystemExit("Sem dados de clima.")
    ontem = dados[-1]
    d = ontem["data"]
    j7  = [x for x in dados if (d - x["data"]).days < 7]
    j30 = [x for x in dados if (d - x["data"]).days < 30]
    # pressao de mancha: HORAS reais de molhamento (UR>=90%, dado horario) por mes,
    # comparadas a media historica de 11 anos (CLIM_MOLH). Ultimos 6 meses.
    pormes = {}  # (y,m) -> [soma_horas, n_dias_com_dado]
    for x in dados:
        if x["molh"] is None: continue
        if (d.year - x["data"].year)*12 + (d.month - x["data"].month) <= 5:
            key = (x["data"].year, x["data"].month)
            pormes.setdefault(key, [0, 0]); pormes[key][0] += x["molh"]; pormes[key][1] += 1
    pres = []
    for (y, m), (soma_h, nd) in sorted(pormes.items())[-6:]:
        norm = round(CLIM_MOLH.get(m, 0) * nd)
        pres.append({"mes": MESES[m-1], "n": round(soma_h), "norm": norm,
                     "dev": (soma_h/norm if norm > 0 else (1.0 if soma_h == 0 else 2.0))})
    # molhamento dos ultimos 15 dias (janela de infeccao ~48h) + nivel de risco fungico
    j15 = [x["molh"] for x in dados if x["molh"] is not None and (d - x["data"]).days < 15]
    molh15 = round(sum(j15))
    risco15 = "alto" if molh15 >= 48 else "moderado" if molh15 >= 24 else "baixo"
    return {
        "ontem": ontem, "data_iso": d.isoformat(), "data_br": d.strftime("%d/%m/%Y"),
        "et0_7d": soma([x["et0"] for x in j7]), "et0_30d": soma([x["et0"] for x in j30]),
        "vpd_7d": media([x["vpd"] for x in j7]),
        "chuva_30d": soma([x["chuva"] for x in j30]),
        "dias_chuva_30d": sum(1 for x in j30 if (x["chuva"] or 0) > 0),
        "tmin_7d_min": min([x["tmin"] for x in j7 if x["tmin"] is not None], default=None),
        "pressao": pres, "molh15": molh15, "risco15": risco15,
    }

# ---------- 2. PREVISAO ----------
WCODE = {0:"☀️",1:"🌤️",2:"⛅",3:"☁️",45:"🌫️",48:"🌫️",51:"🌦️",53:"🌦️",55:"🌧️",
         61:"🌧️",63:"🌧️",65:"🌧️",80:"🌦️",81:"🌧️",82:"⛈️",95:"⛈️"}
def previsao():
    u=(f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
       "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
       "&timezone=America%2FSao_Paulo&forecast_days=6")
    try:
        j = requests.get(u, timeout=30).json()["daily"]
    except Exception as e:
        log(f"Open-Meteo falhou: {e}"); return []
    out=[]
    for i in range(1, min(6, len(j["time"]))):
        dt = datetime.date.fromisoformat(j["time"][i])
        out.append({"label": f"{DIAS[dt.weekday()]} {dt.strftime('%d/%m')}",
            "tmax": round(j["temperature_2m_max"][i]), "tmin": round(j["temperature_2m_min"][i]),
            "chuva": j["precipitation_probability_max"][i], "ic": WCODE.get(j["weathercode"][i], "⛅")})
    return out[:5]

# ---------- 3. BASE DE DOENCAS ----------
def carregar_base():
    txt = {}
    for cult in ["banana","uva","citros","cacau"]:
        partes=[]
        for fp in sorted(glob.glob(str(RAIZ/"base_doencas"/cult/"*.md"))):
            partes.append(pathlib.Path(fp).read_text(encoding="utf-8-sig"))
        txt[cult] = "\n\n".join(partes).replace("﻿", "")
    return txt

# ---------- 4. IA (Sonnet ancorada na base) ----------
SCHEMA = {"type":"object","additionalProperties":False,"properties":{
  "risco_nivel":{"type":"string","enum":["OK","ATENÇÃO","ESTRESSE","ESTRESSE SEVERO"]},
  "risco_frase":{"type":"string"},
  "culturas":{"type":"array","items":{"type":"object","additionalProperties":False,"properties":{
    "cultura":{"type":"string","enum":["banana","uva","citros","cacau"]},
    "nivel":{"type":"string","enum":["verde","amarelo","vermelho"]},
    "acompanhar":{"type":"string"},"doencas":{"type":"string"},
    "pragas":{"type":"string"},"nutricao":{"type":"string"}},
    "required":["cultura","nivel","acompanhar","doencas","pragas","nutricao"]}}},
  "required":["risco_nivel","risco_frase","culturas"]}

def analisar(ag, base):
    import anthropic
    o = ag["ontem"]
    fb = ag.get("fb") or {}
    aviso_fb = ""
    if fb.get("ontem_estimado"):
        aviso_fb = ("\nATENÇÃO: a estação INMET está indisponível — os dados de ONTEM acima são "
                    "ESTIMATIVAS de modelo (Open-Meteo, calibradas), não medições. Nos textos, deixe claro "
                    "que a leitura do dia é 'com base em estimativa' e evite afirmações categóricas sobre o dia.\n")
    elif fb.get("datas"):
        aviso_fb = (f"\nNOTA: {len(fb['datas'])} dia(s) recentes usam estimativa (estação falhou); "
                    f"o acumulado de molhamento inclui {fb['molh_est_h']} h estimadas.\n")
    pres_str = ", ".join(f"{p['mes']}={p['n']}h({'acima' if p['dev']>1.1 else 'abaixo' if p['dev']<0.9 else 'normal'} da media {p['norm']}h)" for p in ag["pressao"])
    # Campo None (ex.: daily do Open-Meteo na borda do horizonte em dia estimado)
    # não pode virar "ET0=None mm" no prompt — a IA leria lixo.
    fv = lambda v: "indisponível" if v is None else v
    ctx = (aviso_fb + f"Pirapora/MG, fruticultura IRRIGADA. Banana é a cultura do produtor; uva/citros/cacau são da região.\n"
        f"Data: {ag['data_br']}. Ontem: ET0={fv(o['et0'])} mm, VPD={fv(o['vpd'])} kPa, Tmax={fv(o['tmax'])}°C, "
        f"Tmin={fv(o['tmin'])}°C, radiação={fv(o['rad'])} MJ, vento={fv(o['vento'])} m/s, chuva={fv(o['chuva'])} mm.\n"
        f"Acumulados: ET0 7d={ag['et0_7d']} mm, 30d={ag['et0_30d']} mm; VPD médio 7d={ag['vpd_7d']} kPa; "
        f"chuva 30d={ag['chuva_30d']} mm em {ag['dias_chuva_30d']} dias; menor Tmin 7d={ag['tmin_7d_min']}°C.\n"
        f"MOLHAMENTO FOLIAR (orvalho, HORAS reais com UR≥90% medidas na estação) — é o DRIVER dos fungos de mancha, NÃO a chuva:\n"
        f"  últimos 15 dias = {ag['molh15']} h de molhamento → risco fúngico de mancha {ag['risco15'].upper()};\n"
        f"  horas por mês vs média histórica de 11 anos: {pres_str}.")
    regras = ("REGRAS OBRIGATÓRIAS:\n"
        "- Só cite doença/praga que esteja nas FICHAS abaixo (por cultura). Nunca invente.\n"
        "- Itens com 'ativo_no_painel: vigilancia' ou 'região livre': NÃO geram alerta — só vigilância.\n"
        "- Tom consultivo, sem alarme. NUNCA diga 'solo seco' (é irrigado); fale em irrigação/fertirrigação.\n"
        "- PAINEL PÚBLICO: comunique APENAS risco/condição e termine em 'monitore/acompanhe/fique atento'. "
        "NUNCA prescreva ação operacional (pulverizar, aplicar fungicida, ensacar, desfolha, dose, produto, intervalo de aplicação) — a decisão é de cada produtor.\n"
        "- Use 'mal de Sigatoka' (não 'Sigatoka negra').\n"
        "- Manchas vêm de ACÚMULO de molhamento (não de um dia). Fungos sobem no úmido; ácaros/tripes/vetores no quente-seco.\n"
        "- CHUVA BAIXA NÃO É MOLHAMENTO BAIXO: em tempo seco pode haver muito ORVALHO (UR≥90% de madrugada). Baseie o risco de mancha/Sigatoka/Pyricularia/Deightoniella NO MOLHAMENTO REAL informado acima (15 dias + vs média histórica), NUNCA na chuva. Se o molhamento está alto/acima do normal, o risco de mancha é ELEVADO mesmo com chuva baixa. Seja COERENTE com a seção de molhamento do painel — não diga 'baixa pressão de molhamento' quando os dados mostram molhamento alto.\n"
        + VPD_FAIXAS_TXT + "\n"
        "- ANÁLISE MULTI-FATOR: a 'risco_frase' do topo e o 'acompanhar' devem INTEGRAR todos os sinais relevantes do dia, não focar só no molhamento. Considere e mencione quando em destaque: molhamento foliar → fungos de mancha; Tmin baixa (<13°C) → frio/estresse na bananeira; VPD/ET0 → demanda hídrica/irrigação; calor + seco → tripes/ácaros. Pondere o conjunto.\n"
        "- Para cada cultura: 'acompanhar' (1 frase do status do dia), 'doencas', 'pragas', 'nutricao' (1-2 frases cada).")
    fichas = "\n\n".join(f"### FICHAS {c.upper()}\n{base[c][:6000]}" for c in ["banana","uva","citros","cacau"])
    schema_txt = json.dumps(SCHEMA, ensure_ascii=False)
    client = anthropic.Anthropic()
    resp = client.messages.create(model=MODELO_IA, max_tokens=4000,
        system="Você é engenheiro agrônomo. Gere alertas climáticos consultivos para um painel comercial, ancorados estritamente nas fichas fornecidas. Responda APENAS com um JSON válido, sem nenhum texto fora do JSON.",
        messages=[{"role":"user","content": f"{ctx}\n\n{regras}\n\nResponda no formato JSON deste schema:\n{schema_txt}\n\nFICHAS:\n{fichas}"}])
    out = next(b.text for b in resp.content if b.type=="text").strip()
    if out.startswith("```"): out = out.split("```",2)[1].lstrip("json").strip()
    i, k = out.find("{"), out.rfind("}")
    return json.loads(out[i:k+1])

# ---------- 5. PATROCINADORES ----------
COTA_CLASSE = {"zenite":"cota-zenite","alisios":"cota-alisios","origem":"cota-origem","bromelia":"cota-bromelia"}
TIER = {"fundador":"tier-fundador","premium":"tier-premium","master":"tier-master","apoio":"tier-apoio"}
def ler_patrocinadores():
    fp = RAIZ/"patrocinadores.json"
    if not fp.exists(): return []
    try: rows = json.loads(fp.read_text(encoding="utf-8"))
    except Exception: return []
    ps = [r for r in rows if str(r.get("ativo", True)).lower() not in ("false","0","nao","não")]
    ps.sort(key=lambda x: x.get("ordem", 99))
    return ps

def b64file(fn):
    if not fn: return ""
    p = RAIZ/"logos"/fn
    return "data:image/png;base64,"+base64.b64encode(p.read_bytes()).decode() if p.exists() else ""

def faixa_patrocinadores(ps, b64):
    if not ps:
        grupo = '<span style="font-size:12px;color:#65766b;font-style:italic;padding:0 12px">Espaço para patrocinadores</span>'
    else:
        itens=[]
        for p in ps:
            cota=str(p.get("cota","")); nc=str(p.get("nome_cota",""))
            tier=TIER.get(cota.lower(),"tier-master")
            cc=COTA_CLASSE.get(nc.lower(),"cota-alisios")
            sel=(cota.capitalize()+" · "+nc.capitalize()) if nc else cota.capitalize()
            itens.append(f'<a class="ac-patroc-item {tier}" href="{p.get("link","#")}" target="_blank">'
                         f'<img src="{b64file(p.get("logo",""))}" alt="{p.get("nome","")}"><span class="ac-cota {cc}">{sel}</span></a>')
        grupo = "".join(itens)
    g = f'<div class="ac-group">{grupo}</div>'
    return (f'<div class="ac-patroc"><div class="ac-patroc-brand"><img src="{b64["sassaki"]}" alt="Sassaki">'
            f'<small>Uma iniciativa</small></div><div class="ac-patroc-vp"><div class="ac-marquee">{g}{g}</div></div></div>')

# ---------- 6. MONTAGEM ----------
def b64logo(name):
    p = RAIZ/"logos"/f"{name}.png"
    if not p.exists(): return ""
    return "data:image/png;base64,"+base64.b64encode(p.read_bytes()).decode()

EMOJI={"banana":"🍌","uva":"🍇","citros":"🍊","cacau":"🍫"}
NOMES={"banana":"Banana Prata","uva":"Uva Niagara","citros":"Citros","cacau":"Cacau"}
TAG={"verde":"Verde","amarelo":"Amarelo","vermelho":"Vermelho"}

def card_cultura(c):
    cl=c["nivel"]
    return (f'<div class="ac-cult" id="{c["cultura"]}"><div class="ac-cult-head {cl}">'
        f'<span class="ac-cult-emoji">{EMOJI[c["cultura"]]}</span><span class="ac-cult-nome">{NOMES[c["cultura"]]}</span>'
        f'<span class="ac-cult-tag">{TAG[cl]}</span></div><div class="ac-cult-body">'
        f'<div class="ac-item"><span class="ic">⚠</span><span><strong>Acompanhar:</strong> {c["acompanhar"]}</span></div>'
        f'<button class="ac-cult-toggle" id="btn-{c["cultura"]}" onclick="det(\'{c["cultura"]}\')">ver detalhes ▾</button><div class="ac-cult-det" id="det-{c["cultura"]}">'
        f'<div class="ac-item nova doenca"><span class="ic">🦠</span><span><strong>Doenças:</strong> {c["doencas"]}</span></div>'
        f'<div class="ac-item nova praga"><span class="ic">🐛</span><span><strong>Pragas:</strong> {c["pragas"]}</span></div>'
        f'<div class="ac-item nova nutri"><span class="ic">🧪</span><span><strong>Nutrição:</strong> {c["nutricao"]}</span></div>'
        f'<div class="ac-protecao">ℹ Informativo — não substitui laudo técnico agronômico local.</div></div></div></div>')

def aviso_fallback_html(fb, ontem_data):
    """Faixa de transparência quando há dia estimado ou sem dado (Plano B)."""
    partes=[]
    if fb.get("ontem_estimado"):
        partes.append(
            f'<div style="background:#fff7e0;border:1px solid #e8a33d;border-radius:8px;padding:10px 14px;margin:6px 0 10px;font-size:13px;color:#7a5a12">'
            f'⚠️ <strong>Estação INMET A545 indisponível em {ontem_data.strftime("%d/%m")} — exibindo estimativa (Open-Meteo).</strong> '
            f'Os valores são aproximados; o dado real volta automaticamente quando a estação se recuperar.</div>')
    elif fb.get("datas"):
        dias=", ".join(d.strftime("%d/%m") for d in fb["datas"])
        partes.append(
            f'<div style="background:#fff7e0;border:1px solid #e8a33d;border-radius:8px;padding:8px 14px;margin:6px 0 10px;font-size:12px;color:#7a5a12">'
            f'⚠️ Dia(s) com estimativa (Open-Meteo) por falha da estação: <strong>{dias}</strong> — o acumulado de molhamento inclui horas estimadas.</div>')
    if fb.get("sem_dado"):
        dias=", ".join(d.strftime("%d/%m") for d in fb["sem_dado"])
        partes.append(
            f'<div style="background:#fdecea;border:1px solid #d9534f;border-radius:8px;padding:8px 14px;margin:6px 0 10px;font-size:12px;color:#8a2620">'
            f'⛔ Sem dado em <strong>{dias}</strong>: estação E estimativa indisponíveis. Estes dias não entram nos acumulados.</div>')
    return "".join(partes)

def montar_html(ag, prev, ia, ps, b64):
    o=ag["ontem"]
    fb=ag.get("fb") or {}
    ontem_estimado=bool(o.get("estimado"))
    nivel_classe={"OK":"verde","ATENÇÃO":"amarelo","ESTRESSE":"vermelho","ESTRESSE SEVERO":"vermelho"}[ia["risco_nivel"]]
    est_tag=' <em style="font-weight:400;font-size:10px;color:#b07a1e">(estimado)</em>' if ontem_estimado else ""
    fonte_rad="Estimativa Open-Meteo" if ontem_estimado else "Estação INMET A545"
    metr=[("ET₀ ontem",br(o["et0"],2),f"mm/dia{est_tag}",f"7d: <strong>{br(ag['et0_7d'])}</strong> · 30d: <strong>{br(ag['et0_30d'])}</strong> mm"),
          ("VPD ontem",br(o["vpd"],2),f"kPa{est_tag}",f"Média 7d: <strong>{br(ag['vpd_7d'],2)}</strong>"),
          ("Temperatura",f"{br(o['tmax'])}° {br(o['tmin'])}°",f"máx / mín °C{est_tag}",f"Status: <strong>{o['status'] or '—'}</strong>"),
          ("Radiação",br(o["rad"],1),f"MJ/m²/dia{est_tag}",fonte_rad),
          ("Vento",round((o["vento"] or 0)*3.6),f"km/h{est_tag}","médio do dia")]
    mh="".join(f'<div class="ac-metric"><div class="ac-metric-label">{n}</div><div class="ac-metric-valor" style="font-size:20px">{v}</div><div class="ac-metric-unit">{u}</div><div class="ac-metric-acum">{a}</div></div>' for n,v,u,a in metr)
    ph="".join(f'<div class="ac-prev-card"><div class="ac-prev-dia">{p["label"]}</div><div class="ac-prev-ic">{p["ic"]}</div><div class="ac-prev-temp"><span class="tmax">{p["tmax"]}°</span> <span class="tmin">{p["tmin"]}°</span></div><div class="ac-prev-chuva">{p["chuva"]}% chuva</div></div>' for p in prev) or '<div style="color:#65766b">previsão indisponível</div>'
    mx=max([p["n"] for p in ag["pressao"]]+[1])
    cor_dev=lambda dev: "#d9534f" if dev>1.1 else ("#e8a33d" if dev>=0.9 else "#7ec98e")
    pb="".join(f'<div class="ac-pb"><div class="ac-pb-val">{p["n"]}h</div><div class="ac-pb-bar" style="height:{max(5,int(p["n"]/mx*100))}%;--c:{cor_dev(p["dev"])}"></div><div class="ac-pb-lb">{p["mes"].capitalize()}</div><div class="ac-pb-norm">méd {p["norm"]}h</div></div>' for p in ag["pressao"])
    r15=ag["risco15"]; cor15={"alto":"#d9534f","moderado":"#e8a33d","baixo":"#7ec98e"}[r15]
    txt15={"alto":"Risco de mancha foliar ELEVADO","moderado":"Risco de mancha foliar MODERADO","baixo":"Risco de mancha foliar BAIXO"}[r15]
    nota_est=f' <em style="font-size:11px">({fb["molh_est_h"]} h estimadas — Plano B)</em>' if fb.get("molh_est_h") else ""
    molh15_html=f'<div class="ac-molh15" style="--c:{cor15}"><span class="ac-molh15-dot"></span><strong>{txt15}</strong> nos últimos 15 dias — {ag["molh15"]} h de molhamento foliar (UR≥90%).{nota_est}</div>'
    preventiva=('<div class="ac-pressao-txt">🍌 Cachos em formação agora sob maior pressão de mancha — o sintoma costuma aparecer ~60–70 dias depois. <strong>Acompanhe.</strong></div>' if r15=="alto" else "")
    cult={c["cultura"]:c for c in ia["culturas"]}
    cards="".join(card_cultura(cult[k]) for k in ["banana","uva","citros","cacau"] if k in cult)
    fonte_ontem=("⚠ estimativa Open-Meteo (estação indisponível)" if ontem_estimado
                 else "estação INMET A545 (real)")
    metodo_fb=('<p><strong>Plano B (estação indisponível):</strong> se a estação falhar, o dia entra como '
               '<em>estimativa</em> do Open-Meteo, sempre marcada — molhamento com limiar calibrado '
               '(UR_modelo≥76% ≡ UR_estação≥90%) e alerta de frio com Tmín_modelo&lt;16 °C (≡ Tmín_estação&lt;13 °C), '
               'calibrados contra 76 dias reais da estação. O dado real substitui a estimativa automaticamente '
               'quando a estação volta.'
               + (f' Dias recentes estimados: {", ".join(d.strftime("%d/%m") for d in fb["datas"])}.' if fb.get("datas") else " Nenhum dia estimado em uso no momento.")
               + '</p>')
    tpl=(RAIZ/"template"/"painel.html").read_text(encoding="utf-8")
    rep={"«FAIXA»":faixa_patrocinadores(ps,b64),"«DATA»":ag["data_br"],
         "«RISCO_CLASSE»":nivel_classe,"«RISCO_NIVEL»":ia["risco_nivel"],"«RISCO_FRASE»":ia["risco_frase"],
         "«METRICAS»":mh,"«PREVISAO»":ph,"«PRESSAO»":pb,"«MOLH15»":molh15_html,"«PREVENTIVA»":preventiva,"«CULTURAS»":cards,
         "«FONTE_ONTEM»":fonte_ontem,"«AVISO_FALLBACK»":aviso_fallback_html(fb, o["data"]),"«METODO_FALLBACK»":metodo_fb,
         "«CHUVA30»":br(ag["chuva_30d"]),"«DIASCHUVA»":str(ag["dias_chuva_30d"])}
    for k,v in rep.items(): tpl=tpl.replace(k,str(v))
    return tpl

# ---------- 7. PUBLICAR ----------
def publicar(html):
    auth = os.environ["WP_AUTH"].replace("﻿", "").strip()
    if not auth.lower().startswith("basic"): auth="Basic "+auth
    h={"Content-Type":"application/json","Authorization":auth,"Accept":"application/json",
       "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 AgroClima-bot"}
    r=requests.put(WP_URL, headers=h, data=json.dumps({"content":html,"template":WP_TEMPLATE}).encode("utf-8"), timeout=60)
    r.raise_for_status()
    return r.status_code

# ---------- 8. LOG / ISSUE ----------
def gravar_log(d):
    p=RAIZ/"logs"; p.mkdir(exist_ok=True)
    (p/f"{datetime.date.today().isoformat()}.json").write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")

def abrir_issue(titulo, corpo):
    tok=os.environ.get("GITHUB_TOKEN"); repo=os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo: return
    h={"Authorization":f"token {tok}","Accept":"application/vnd.github+json"}
    # Dedup: se já existe issue ABERTA com o mesmo título, não abre outra —
    # permite alertar todo dia (>=48h) sem virar spam.
    try:
        abertas=requests.get(f"https://api.github.com/repos/{repo}/issues?state=open&labels=painel-falha&per_page=50",
                             headers=h, timeout=30).json()
        if any(i.get("title")==titulo for i in abertas if isinstance(i, dict)):
            return
    except Exception as e:
        log(f"abrir_issue: checagem de duplicata falhou ({e}) — abrindo mesmo assim")
    r=requests.post(f"https://api.github.com/repos/{repo}/issues", headers=h,
        data=json.dumps({"title":titulo,"body":corpo,"labels":["painel-falha"]}), timeout=30)
    if r.status_code >= 300:
        log(f"abrir_issue: falhou HTTP {r.status_code}")

# ---------- MAIN ----------
def main():
    log(f"Modo: {'DRY-RUN' if DRY else 'PUBLICAR'}")
    dados=ler_clima()
    # Plano B: dias faltantes (estação falhou) viram estimativa Open-Meteo MARCADA.
    # "Hoje" no fuso da fazenda (UTC-3, Brasil sem horário de verão) — o Actions roda em UTC.
    hoje_sp=(datetime.datetime.utcnow()-datetime.timedelta(hours=3)).date()
    dados,fb=aplicar_fallback(dados,hoje_sp)
    ag=agregar(dados)
    ag["fb"]=fb
    fonte_ontem="estimado (Open-Meteo)" if ag["ontem"].get("estimado") else "estação"
    log(f"Clima ok — ontem {ag['data_br']} (ET0 {ag['ontem']['et0']}, fonte: {fonte_ontem})")
    # alerta de estação fora por 48h+ (2+ dias consecutivos em fallback).
    # >=2 (não ==2): se o workflow pular justamente o 2º dia, o alerta ainda sai;
    # a deduplicação por título aberto em abrir_issue evita spam diário.
    if fb["consec"]>=2:
        abrir_issue("Painel: estação INMET A545 fora do ar há 48h+",
            f"{fb['consec']} dias consecutivos sem dado da estação (fallback Open-Meteo em uso): "
            f"{', '.join(d.strftime('%d/%m') for d in fb['datas'])}. Verificar estação/coletor.")
    # falha dupla: nem estação nem estimativa
    if fb["sem_dado"]:
        abrir_issue("Painel: dia SEM DADO (estação e Open-Meteo indisponíveis)",
            f"Dia(s) sem nenhum dado: {', '.join(d.strftime('%d/%m') for d in fb['sem_dado'])}.")
    # checagem de frescor: dado de ontem deve ser recente (mesmo com fallback)
    atraso=(hoje_sp-ag["ontem"]["data"]).days
    if atraso>2:
        abrir_issue("Painel: dado do INMET atrasado",
            f"Último dia disponível: {ag['data_br']} ({atraso} dias atrás). Painel gerado com o último disponível.")
    prev=previsao(); log(f"Previsão: {len(prev)} dias")
    base=carregar_base()
    ia=analisar(ag,base); log(f"IA: risco {ia['risco_nivel']}, {len(ia['culturas'])} culturas")
    ps=ler_patrocinadores(); log(f"Patrocinadores: {len(ps)}")
    b64={"sassaki":b64logo("sassaki")}
    html=montar_html(ag,prev,ia,ps,b64)
    out=RAIZ/"saida"; out.mkdir(exist_ok=True)
    (out/"painel.html").write_text(html,encoding="utf-8")
    log(f"HTML gerado: {len(html)} bytes -> saida/painel.html")
    status="dry-run"
    if not DRY:
        code=publicar(html); status=f"publicado {code}"; log(f"WordPress: {code}")
    gravar_log({"data":ag["data_iso"],"risco":ia["risco_nivel"],"previsao_dias":len(prev),
                "patrocinadores":len(ps),"status":status,
                "fonte_ontem":fonte_ontem,
                "fallback_datas":[d.isoformat() for d in fb["datas"]],
                "sem_dado":[d.isoformat() for d in fb["sem_dado"]],
                "executado":datetime.datetime.utcnow().isoformat()})

if __name__=="__main__":
    try: main()
    except Exception as e:
        log(f"ERRO: {e}")
        abrir_issue("Painel AgroClima: falha na execução", f"```\n{e}\n```")
        raise
