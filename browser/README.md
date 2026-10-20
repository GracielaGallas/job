# Portais que só abrem num navegador de verdade

Indeed, Glassdoor e GermanTechJobs bloqueiam acesso automatizado de servidores (tela
anti-bot). Eles entram na busca quando o Claude roda **no seu computador** (Claude Desktop
com o navegador embutido, ou Claude in Chrome) e lê as páginas no seu navegador.

## Fluxo (para o Claude)

1. Abrir cada busca abaixo numa aba nova, lendo a página como texto. Se aparecer
   captcha ou verificação, **parar e pedir para a Graciela resolver**: nunca tentar contornar.
2. Para cada vaga da primeira página (e da segunda, se a primeira vier cheia), anotar
   título, empresa, local e link. Abrir a vaga só se for preciso para conferir idioma.
3. Salvar em `browser/<data>.json` no formato abaixo e rodar:
   `python3 search.py --extra browser/<data>.json`
   As vagas passam pelos mesmos filtros, score e remoção de duplicadas das outras fontes.
4. Fechar as abas abertas.

## Buscas

| Portal | URL |
|---|---|
| Indeed — Berlim | https://de.indeed.com/jobs?q=QA+Engineer&l=Berlin&fromage=14 |
| Indeed — remoto | https://de.indeed.com/jobs?q=QA+Engineer&l=Deutschland&sc=0kf%3Aattr%28DSQF7%29%3B&fromage=14 |
| Indeed — Testautomatisierung | https://de.indeed.com/jobs?q=Testautomatisierung&l=Berlin&fromage=14 |
| Glassdoor — Berlim | https://www.glassdoor.de/Job/berlin-qa-engineer-jobs-SRCH_IL.0,6_IC2622109_KO7,18.htm?fromAge=14 |
| GermanTechJobs — QA | https://germantechjobs.de/jobs/QA/all |

## Formato do JSON

```json
[
  {
    "source": "Indeed",
    "title": "Senior QA Engineer (m/w/d)",
    "company": "Example GmbH",
    "location": "Berlin",
    "url": "https://de.indeed.com/viewjob?jk=...",
    "published": "2026-10-18",
    "remote": false,
    "description": ""
  }
]
```

`published` e `description` são opcionais (`description` melhora o score de skills e idioma).
