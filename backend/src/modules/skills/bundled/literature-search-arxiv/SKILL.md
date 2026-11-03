---
name: literature-search-arxiv
description: >
  Search for scientific papers, preprints, and publications on arXiv. Extract
  metadata, abstracts, and download full-text PDFs or HTML versions of papers.
  Use when the user asks to find research papers, literature, or specific arXiv
  IDs.
version: 1.0.0
---

# arXiv Search and Retrieval

## Core Rules

- **Terms of Use**: You MUST respect arXiv's Terms of Use (`https://info.arxiv.org/help/api/index.html`).
  - Maximum 1 request every 3 seconds.
  - The provided utility scripts below `/workspace/skills/literature-search-arxiv/scripts/` handle rate limiting automatically. Always use these scripts rather than writing ad-hoc curl requests.
- If this skill is used, mention arXiv in the output AND list the URLs of all papers that were used in producing the output.

## Utility Scripts

Once loaded via `skill_load(name="literature-search-arxiv")`, the scripts are located in `/workspace/skills/literature-search-arxiv/scripts/` and run with standard `python3` (zero external package dependencies required).

**1. Search and Extract Metadata**

Search arXiv and return a clean JSON array of matching papers:

```bash
python3 /workspace/skills/literature-search-arxiv/scripts/search_arxiv.py \
  --query "au:einstein AND ti:relativity" \
  --max_results 5 > /tmp/arxiv_search_results.json
```

> **Important**: Limit `--max_results` (e.g., 5–10) or paginate carefully using `--start`. Redirect output to a file or inspect cleanly.

*Returned Metadata:* JSON results include `id`, `title`, `summary`, `published`, `authors`, `pdf_url`, `primary_category`, `doi`, `journal_ref`, and `comment`.

*Options:*
- `--query`: Search string. See [references/query_syntax.md](references/query_syntax.md) for advanced syntax (`ti:`, `au:`, `abs:`, `cat:`, `AND`, `OR`, `ANDNOT`).
- `--id_list`: Comma-separated list of arXiv IDs to fetch directly (e.g., `1706.03762v5`).
- `--start`: Pagination offset (default 0).
- `--max_results`: Number of results to return (default 10).
- `--sort_by`: `relevance`, `lastUpdatedDate`, or `submittedDate`.
- `--sort_order`: `ascending` or `descending`.

**2. Download Paper (PDF or HTML)**

Download the full text of a paper to your local workspace:

```bash
python3 /workspace/skills/literature-search-arxiv/scripts/download_paper.py \
  --id 1706.03762 --format html --output /workspace/attention.html
```

*Options:*
- `--id`: The arXiv ID (e.g., `1706.03762` or `1706.03762v5`).
- `--format`: `pdf` or `html` (HTML is available for many newer papers and is easy to read directly).
- `--output`: Filepath to save the downloaded document.

**3. Download Paper Source (tar.gz)**

Download the LaTeX source files of a paper:

```bash
python3 /workspace/skills/literature-search-arxiv/scripts/download_paper_source.py \
  --id 2010.11645 --output /workspace/source.tar.gz
```

> **Safe Extraction**: Always extract into a dedicated directory: `mkdir -p /workspace/paper_source && tar -xzf /workspace/source.tar.gz -C /workspace/paper_source`

## Reference

- **Advanced Query Syntax**: See `/workspace/skills/literature-search-arxiv/references/query_syntax.md` for prefixes (`au`, `ti`, `abs`, `cat`), booleans, and date filtering.
