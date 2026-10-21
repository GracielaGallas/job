# job

Busca de vagas de QA em portais alemães, com critérios tirados do CV, e gera uma lista
ordenada com os links.

## Como usar

```bash
python3 search.py                  # busca completa (últimos 14 dias)
python3 search.py --days 7         # só a última semana
python3 search.py --berlin-only    # só Berlim
python3 search.py --remote-only    # só remoto
python3 search.py --min-score 70   # só as que mais combinam
python3 search.py --sources LinkedIn,XING
```

Precisa só de Python 3.11+ (sem dependências). O resultado vai para `results/<data>.md`.
`data/seen.json` guarda as vagas já vistas, para marcar as novas com 🆕 na busca seguinte.
`data/applied.json` lista as vagas em que você já aplicou: elas não aparecem mais nos resultados.

## Fontes

| Fonte | Como | Observação |
|---|---|---|
| Bundesagentur für Arbeit | API pública (`rest.arbeitsagentur.de`, v6) | Berlim + raio, e home office na Alemanha toda |
| Arbeitnow | API pública | Traz descrição completa, então o score de skills e idioma é mais preciso |
| LinkedIn | busca pública sem login + página pública de cada vaga | |
| XING | página de busca pública | Sem descrição |

Indeed, Glassdoor e GermanTechJobs bloqueiam acesso automatizado (403) e ficaram de fora.

## Critérios (`profile.toml`)

Tudo que define a busca está em `profile.toml` e pode ser editado:

- **queries**: termos buscados em cada portal.
- **title_strong / title_weak / software_signal**: o título precisa indicar QA de software.
  Títulos genéricos ("Qualitätssicherung", "Test Manager") só entram quando há sinal de
  software, para não trazer QA de fábrica ou laboratório.
- **title_none**: descarta júnior, estágio, Werkstudent, hardware, pharma etc.
- **exclude**: lido na descrição de cada vaga (o script abre a vaga no LinkedIn, XING e
  Bundesagentur). A vaga sai quando pede alemão acima de B1 (B2, C1, fluente…) ou a descrição é em alemão, carteira de motorista, é de outro setor
  (aviação, embedded, hardware, automotive, rede…), ou
  quando é fora de Berlim e híbrida/presencial (ou não diz que é remota). As descartadas
  aparecem no fim do resultado com o trecho que causou a exclusão.
- **score** (0–100): base 50, sênior/lead +15, skills do CV +5 cada (até 25), vaga recente
  até +7, descrição em inglês +3.

O resultado é uma lista em três faixas (80+, 65–79, abaixo de 65), legível no GitHub e no celular.
