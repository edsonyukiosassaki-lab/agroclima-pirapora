#!/usr/bin/env python3
"""
Plano B da estação INMET A545 — estima dias FALTANTES com o Open-Meteo.

Só entra em ação quando um dia não existe na tabela `clima` (ou seja, quando a
estação falhou ou o coletor recusou o dia por qualidade). A estimativa vive
apenas na execução do painel: NUNCA é gravada na planilha mestre nem no Supabase.

Calibração (PARTE ZERO, 15/07/2026 — 76 dias reais comparados; ver
relatorio-parte-zero-fallback.md na pasta do projeto):
  • A UR do modelo é ~10 pontos mais SECA que a estação (efeito do vale do rio).
    Molhamento estimado = horas com UR_modelo >= LIMIAR_UR_MODELO (76%),
    equivalente calibrado do UR_estacao >= 90% (desvio total -2% no período).
    Erro típico do dia individual: ±2,6 h.
  • A Tmín do modelo é ~2 °C mais QUENTE que a estação. Possível frio no dia
    estimado quando Tmin_modelo < LIMIAR_FRIO_MODELO (16 °C) — pegou 10/11 dos
    dias reais de Tmín<13 °C, com 2 falsos alarmes.
  • Tmáx: viés -0,8 °C · ET₀: viés +0,5 mm/dia — usados direto, com etiqueta.

Fontes: dias recentes (≤7) = API de previsão com past_days (sem atraso);
dias mais antigos = arquivo ERA5 (~5 dias de atraso, serve p/ reconstruir).
"""
import datetime
import requests

LAT, LON = -17.35, -44.91
LIMIAR_UR_MODELO   = 76    # equivale a UR_estacao >= 90% (calibrado na PARTE ZERO)
LIMIAR_FRIO_MODELO = 16.0  # equivale a Tmin_estacao < 13°C (calibrado na PARTE ZERO)

_HOURLY = "relative_humidity_2m,temperature_2m,vapour_pressure_deficit,wind_speed_10m"
_DAILY  = "et0_fao_evapotranspiration,temperature_2m_max,temperature_2m_min,shortwave_radiation_sum,precipitation_sum"


def _buscar(url_base, ini, fim, extra=""):
    url = (f"{url_base}?latitude={LAT}&longitude={LON}"
           f"&hourly={_HOURLY}&daily={_DAILY}"
           f"&timezone=America%2FSao_Paulo&wind_speed_unit=ms{extra}"
           f"&start_date={ini}&end_date={fim}")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def _montar_dias(j, quero):
    """Converte a resposta do Open-Meteo em linhas no formato da tabela clima."""
    horas = {}  # date -> list[(ur, vpd, vento)]
    for i, ts in enumerate(j["hourly"]["time"]):
        d = datetime.date.fromisoformat(ts[:10])
        if d not in quero:
            continue
        horas.setdefault(d, []).append((
            j["hourly"]["relative_humidity_2m"][i],
            j["hourly"]["vapour_pressure_deficit"][i],
            j["hourly"]["wind_speed_10m"][i],
        ))
    diarios = {}
    for i, ds in enumerate(j["daily"]["time"]):
        d = datetime.date.fromisoformat(ds)
        if d in quero:
            diarios[d] = i
    out = {}
    for d, idx in diarios.items():
        hs = horas.get(d, [])
        urs   = [h[0] for h in hs if h[0] is not None]
        vpds  = [h[1] for h in hs if h[1] is not None]
        vents = [h[2] for h in hs if h[2] is not None]
        if len(urs) < 20:   # estimativa incompleta não serve (falha dupla → "sem dado")
            continue
        tmin = j["daily"]["temperature_2m_min"][idx]
        out[d] = {
            "data": d,
            "et0":  j["daily"]["et0_fao_evapotranspiration"][idx],
            "vpd":  round(sum(vpds)/len(vpds), 2) if vpds else None,
            "tmax": j["daily"]["temperature_2m_max"][idx],
            "tmin": tmin,
            "ur":   round(sum(urs)/len(urs), 1),
            "rad":  j["daily"]["shortwave_radiation_sum"][idx],
            "vento": round(sum(vents)/len(vents), 2) if vents else None,
            "chuva": j["daily"]["precipitation_sum"][idx],
            "molh": sum(1 for u in urs if u >= LIMIAR_UR_MODELO),
            "status": "ESTIMADO" + (" · possível frio" if tmin is not None and tmin < LIMIAR_FRIO_MODELO else ""),
            "estimado": True,
        }
    return out


def estimar_dias(datas, hoje=None):
    """datas: lista de datetime.date faltantes → dict[date -> linha estimada].
    Dia que nem o Open-Meteo conseguir estimar fica FORA do retorno (falha dupla)."""
    if not datas:
        return {}
    hoje = hoje or datetime.date.today()
    quero = set(datas)
    recentes = {d for d in quero if (hoje - d).days <= 7}
    antigos  = quero - recentes
    out = {}
    if recentes:
        try:
            j = _buscar("https://api.open-meteo.com/v1/forecast",
                        min(recentes).isoformat(), max(recentes).isoformat())
            out.update(_montar_dias(j, recentes))
        except Exception as e:
            print(f"  fallback: previsão past_days falhou: {e}", flush=True)
    if antigos:
        try:
            j = _buscar("https://archive-api.open-meteo.com/v1/archive",
                        min(antigos).isoformat(), max(antigos).isoformat())
            out.update(_montar_dias(j, antigos))
        except Exception as e:
            print(f"  fallback: arquivo ERA5 falhou: {e}", flush=True)
    return out
