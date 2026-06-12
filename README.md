# AgroClima Pirapora — automação do painel (substitui o Make)

Gera diariamente o painel climático de Pirapora e publica na página WordPress **1649**.

## Pipeline
```
Estação INMET A545 (planilha BD_clima_auto) ─┐
Open-Meteo (previsão 5 dias) ────────────────┤→ gerar_painel.py → HTML → PUT wp-json/wp/v2/pages/1649
Base de doenças/pragas (base_doencas/) ───────┤        ↑
Planilha de patrocinadores ──────────────────┘   IA Claude Sonnet 4.6 (análise ancorada na base)
```

- **Observado** (ontem/7d/30d): estação real INMET (mesmos dados do ERP), via planilha `BD_clima_auto`. **Não toca no banco central do ERP.**
- **Previsão**: Open-Meteo (lat -17,35 / lon -44,91).
- **IA**: `claude-sonnet-4-6`, ancorada nas fichas de `base_doencas/` (só cita doença/praga que tem ficha).
- **Patrocinadores**: planilha Google editável pelo Edson (aba `patrocinadores`), classificados por cota; faixa rolando na 1ª dobra.

## Rodar
- Diário automático: workflow `.github/workflows/diario.yml` (06h BRT).
- Teste sem publicar: `python gerar_painel.py --dry-run` (salva `saida/painel.html`).
- Publicar: `python gerar_painel.py --publicar`.

## Segurança
Segredos em GitHub Secrets: `ANTHROPIC_API_KEY`, `WP_AUTH` (Basic), `GOOGLE_CREDENTIALS`.
Nada de chave no código.

## Rede de segurança
- Log de execução em `logs/` (commit automático).
- Se faltar o dado de ontem: usa o último dia disponível + aviso, e abre **Issue** no GitHub.
- Botão manual: `workflow_dispatch`.
