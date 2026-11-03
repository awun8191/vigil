---
name: literature-search-openalex
description: >
  Query the OpenAlex scholarly database for research papers, authors,
  institutions, topics, sources, publishers, funders, geo-locations, and
  keywords. Use when searching academic papers, resolving DOIs, downloading
  open-access PDFs, finding an author's publications, aggregating bibliometric
  data (citation counts, h-index, impact factor), exploring the research
  taxonomies, or performing DOI lookups.
version: 1.0.0
---

# OpenAlex Scholarly Research Skill

## Core Rules

1. **List Sources**: If this skill is used, mention OpenAlex in the output AND list the URLs or DOIs of all papers that were used in producing the output.
2. **Resolve before filter**: NEVER filter by name. Always `resolve` a name to an ID first, then use that ID in `--filter`.
3. **Use the CLI**: Once loaded via `skill_load(name="literature-search-openalex")`, invoke `/workspace/skills/literature-search-openalex/scripts/openalex_cli.py` with `python3` (zero external package dependencies required).
4. **No fabrication**: Never invent OpenAlex IDs or DOIs. Use `resolve`/`get` to look them up. Report empty results accurately.
5. **Keep output small**: Always use `--select` and `--per-page 5` to `10` for overview queries.

## CLI Reference

```bash
python3 /workspace/skills/literature-search-openalex/scripts/openalex_cli.py [--api-key KEY] <command> [flags]
```

Entity types (shared across commands): `works`, `authors`, `sources`, `institutions`, `topics`, `domains`, `fields`, `subfields`, `sdgs`, `countries`, `continents`, `languages`, `keywords`, `publishers`, `funders`, `work-types`, `source-types`, `institution-types`, `licenses`

### Commands

- **`resolve <entity> <query>`** — Name → ID candidates. Returns `id`, `display_name`, `hint`. Use `--per-page N` for more candidates.
- **`get <entity> <id>`** — Full metadata for one entity. Accepts short ID (`W2741809807`), full URL, or DOI URL (`https://doi.org/...`). Use `--select` to limit fields.
- **`filter <entity>`** — Search/filter entities:
  - `--search <query>`: Full-text search
  - `--filter <expr>`: Filter expressions (`,` for AND, `|` for OR)
  - `--sort <field:dir>`: Sort results (e.g., `cited_by_count:desc`)
  - `--select <fields>`: Limit fields returned in output
  - `--group-by <field>`: Aggregate results by a field
  - `--per-page <N>`: Results per page (default 25, max 100)
  - `--page <N>`: Page number
- **`download-pdf <work-id> <output-path>`** — Download open-access PDF for a work.
- **`rate-limit`** — Check current rate limit and daily budget status.

## Entity References

Consult `/workspace/skills/literature-search-openalex/references/` for valid filter, sort, and group-by fields per entity (`works.md`, `authors.md`, `sources.md`, `institutions.md`, `topics.md`, `taxonomy.md`).

## Common Workflows

```bash
# Author's works (resolve -> filter)
python3 /workspace/skills/literature-search-openalex/scripts/openalex_cli.py resolve authors "Geoffrey Hinton"
python3 /workspace/skills/literature-search-openalex/scripts/openalex_cli.py filter works \
  --filter "authorships.author.id:A5108093963" \
  --sort "cited_by_count:desc" \
  --select "id,display_name,publication_year,cited_by_count,doi" \
  --per-page 10

# DOI lookup
python3 /workspace/skills/literature-search-openalex/scripts/openalex_cli.py get works "https://doi.org/10.1038/s41586-021-03819-2"
```
