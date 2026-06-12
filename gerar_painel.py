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

SHEET_ID   = "1oaqeRuwsQ7xTpM4CWv2PqC7GlTPHRCZGD5882IveCM4"   # AgroClima (BD_clima_auto)
ABA_CLIMA  = "BD_clima_auto"
ABA_PATRO  = "patrocinadores"
WP_URL     = "https://sassakiagronegocios.com.br/wp-json/wp/v2/pages/1649"
LAT, LON   = -17.35, -44.91
MODELO_IA  = "claude-sonnet-4-6"
RAIZ       = pathlib.Path(__file__).parent
DRY        = "--dry-run" in sys.argv or "--publicar" not in sys.argv

MESES = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
DIAS  = ["seg","ter","qua","qui","sex","sáb","dom"]

def log(m): print(f"  {m}", flush=True)

def f(v):
    try: return float(str(v).replace(",", ".")) if str(v).strip() not in ("","-","nan","None") else None
    except: return None

# ---------- 1. CLIMA (estacao real INMET, via Supabase — somente leitura) ----------
SUPA_URL = os.environ.get("SUPABASE_URL", "https://fxkdjzguyxtbfadmoemg.supabase.co")
SUPA_KEY = os.environ.get("SUPABASE_KEY", "")

def ler_clima():
    cols = "data,et0_mm,vpd_kpa,temp_max_c,temp_min_c,umidade_relativa_pct,radiacao_solar_mj,vento_ms,precipitacao_mm,status"
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
            "status": x.get("status") or ""})
    dados.sort(key=lambda d: d["data"])
    return dados

def soma(seq): seq=[x for x in seq if x is not None]; return round(sum(seq),1) if seq else 0.0
def media(seq): seq=[x for x in seq if x is not None]; return round(sum(seq)/len(seq),2) if seq else None

def agregar(dados):
    if not dados: raise SystemExit("Sem dados de clima.")
    ontem = dados[-1]
    d = ontem["data"]
    j7  = [x for x in dados if (d - x["data"]).days < 7]
    j30 = [x for x in dados if (d - x["data"]).days < 30]
    # pressao acumulada de mancha: dias UR>=80 por mes (ultimos 6 meses)
    pormes = {}
    for x in dados:
        if (d.year - x["data"].year)*12 + (d.month - x["data"].month) <= 5:
            key = (x["data"].year, x["data"].month)
            pormes.setdefault(key, 0)
            if x["ur"] is not None and x["ur"] >= 80: pormes[key] += 1
    pres = [{"mes": MESES[m-1], "n": n} for (y,m), n in sorted(pormes.items())][-6:]
    return {
        "ontem": ontem, "data_iso": d.isoformat(), "data_br": d.strftime("%d/%m/%Y"),
        "et0_7d": soma([x["et0"] for x in j7]), "et0_30d": soma([x["et0"] for x in j30]),
        "vpd_7d": media([x["vpd"] for x in j7]),
        "chuva_30d": soma([x["chuva"] for x in j30]),
        "dias_chuva_30d": sum(1 for x in j30 if (x["chuva"] or 0) > 0),
        "tmin_7d_min": min([x["tmin"] for x in j7 if x["tmin"] is not None], default=None),
        "pressao": pres,
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
    pres_str = ", ".join(f"{p['mes']}={p['n']}" for p in ag["pressao"])
    ctx = (f"Pirapora/MG, fruticultura IRRIGADA. Banana é a cultura do produtor; uva/citros/cacau são da região.\n"
        f"Data: {ag['data_br']}. Ontem: ET0={o['et0']} mm, VPD={o['vpd']} kPa, Tmax={o['tmax']}°C, "
        f"Tmin={o['tmin']}°C, radiação={o['rad']} MJ, vento={o['vento']} m/s, chuva={o['chuva']} mm.\n"
        f"Acumulados: ET0 7d={ag['et0_7d']} mm, 30d={ag['et0_30d']} mm; VPD médio 7d={ag['vpd_7d']} kPa; "
        f"chuva 30d={ag['chuva_30d']} mm em {ag['dias_chuva_30d']} dias; menor Tmin 7d={ag['tmin_7d_min']}°C.\n"
        f"Pressão de molhamento (dias UR≥80/mês): {pres_str}.")
    regras = ("REGRAS OBRIGATÓRIAS:\n"
        "- Só cite doença/praga que esteja nas FICHAS abaixo (por cultura). Nunca invente.\n"
        "- Itens com 'ativo_no_painel: vigilancia' ou 'região livre': NÃO geram alerta — só vigilância.\n"
        "- Tom consultivo, sem alarme. NUNCA diga 'solo seco' (é irrigado); fale em irrigação/fertirrigação.\n"
        "- Use 'mal de Sigatoka' (não 'Sigatoka negra').\n"
        "- Manchas vêm de ACÚMULO de molhamento (não de um dia). Fungos sobem no úmido; ácaros/tripes/vetores no quente-seco.\n"
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
        f'<button class="ac-cult-toggle" onclick="det(this)">ver detalhes ▾</button><div class="ac-cult-det">'
        f'<div class="ac-item nova doenca"><span class="ic">🦠</span><span><strong>Doenças:</strong> {c["doencas"]}</span></div>'
        f'<div class="ac-item nova praga"><span class="ic">🐛</span><span><strong>Pragas:</strong> {c["pragas"]}</span></div>'
        f'<div class="ac-item nova nutri"><span class="ic">🧪</span><span><strong>Nutrição:</strong> {c["nutricao"]}</span></div>'
        f'<div class="ac-protecao">ℹ Informativo — não substitui laudo técnico agronômico local.</div></div></div></div>')

def montar_html(ag, prev, ia, ps, b64):
    o=ag["ontem"]
    nivel_classe={"OK":"verde","ATENÇÃO":"amarelo","ESTRESSE":"vermelho","ESTRESSE SEVERO":"vermelho"}[ia["risco_nivel"]]
    metr=[("ET₀ ontem",o["et0"],"mm/dia",f"7d: <strong>{ag['et0_7d']}</strong> · 30d: <strong>{ag['et0_30d']}</strong> mm"),
          ("VPD ontem",o["vpd"],"kPa",f"Média 7d: <strong>{ag['vpd_7d']}</strong>"),
          ("Temperatura",f"{o['tmax']}° {o['tmin']}°","máx / mín °C",f"Status: <strong>{o['status'] or '—'}</strong>"),
          ("Radiação",o["rad"],"MJ/m²/dia","Estação INMET A545"),
          ("Vento",round((o["vento"] or 0)*3.6),"km/h","médio do dia")]
    mh="".join(f'<div class="ac-metric"><div class="ac-metric-label">{n}</div><div class="ac-metric-valor" style="font-size:20px">{v}</div><div class="ac-metric-unit">{u}</div><div class="ac-metric-acum">{a}</div></div>' for n,v,u,a in metr)
    ph="".join(f'<div class="ac-prev-card"><div class="ac-prev-dia">{p["label"]}</div><div class="ac-prev-ic">{p["ic"]}</div><div class="ac-prev-temp"><span class="tmax">{p["tmax"]}°</span> <span class="tmin">{p["tmin"]}°</span></div><div class="ac-prev-chuva">{p["chuva"]}% chuva</div></div>' for p in prev) or '<div style="color:#65766b">previsão indisponível</div>'
    mx=max([p["n"] for p in ag["pressao"]]+[1])
    pb="".join(f'<div class="ac-pb"><div class="ac-pb-val">{p["n"]}</div><div class="ac-pb-bar" style="height:{max(5,int(p["n"]/mx*100))}%;--c:{("#7ec98e" if p["n"]<5 else "#e8a33d" if p["n"]<10 else "#d9534f")}"></div><div class="ac-pb-lb">{p["mes"].capitalize()}</div></div>' for p in ag["pressao"])
    cult={c["cultura"]:c for c in ia["culturas"]}
    cards="".join(card_cultura(cult[k]) for k in ["banana","uva","citros","cacau"] if k in cult)
    tpl=(RAIZ/"template"/"painel.html").read_text(encoding="utf-8")
    rep={"«FAIXA»":faixa_patrocinadores(ps,b64),"«DATA»":ag["data_br"],
         "«RISCO_CLASSE»":nivel_classe,"«RISCO_NIVEL»":ia["risco_nivel"],"«RISCO_FRASE»":ia["risco_frase"],
         "«METRICAS»":mh,"«PREVISAO»":ph,"«PRESSAO»":pb,"«CULTURAS»":cards,
         "«CHUVA30»":str(ag["chuva_30d"]),"«DIASCHUVA»":str(ag["dias_chuva_30d"])}
    for k,v in rep.items(): tpl=tpl.replace(k,str(v))
    return tpl

# ---------- 7. PUBLICAR ----------
def publicar(html):
    auth = os.environ["WP_AUTH"]
    if not auth.lower().startswith("basic"): auth="Basic "+auth
    r=requests.put(WP_URL, headers={"Content-Type":"application/json","Authorization":auth},
                   data=json.dumps({"content":html}), timeout=60)
    r.raise_for_status()
    return r.status_code

# ---------- 8. LOG / ISSUE ----------
def gravar_log(d):
    p=RAIZ/"logs"; p.mkdir(exist_ok=True)
    (p/f"{datetime.date.today().isoformat()}.json").write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")

def abrir_issue(titulo, corpo):
    tok=os.environ.get("GITHUB_TOKEN"); repo=os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo: return
    requests.post(f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization":f"token {tok}","Accept":"application/vnd.github+json"},
        data=json.dumps({"title":titulo,"body":corpo,"labels":["painel-falha"]}), timeout=30)

# ---------- MAIN ----------
def main():
    log(f"Modo: {'DRY-RUN' if DRY else 'PUBLICAR'}")
    dados=ler_clima()
    ag=agregar(dados)
    log(f"Clima ok — ontem {ag['data_br']} (ET0 {ag['ontem']['et0']})")
    # checagem de frescor: dado de ontem deve ser recente
    atraso=(datetime.date.today()-ag["ontem"]["data"]).days
    if atraso>2:
        abrir_issue("Painel: dado do INMET atrasado",
            f"Último dia na planilha: {ag['data_br']} ({atraso} dias atrás). Painel gerado com o último disponível.")
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
                "patrocinadores":len(ps),"status":status,"executado":datetime.datetime.utcnow().isoformat()})

if __name__=="__main__":
    try: main()
    except Exception as e:
        log(f"ERRO: {e}")
        abrir_issue("Painel AgroClima: falha na execução", f"```\n{e}\n```")
        raise
