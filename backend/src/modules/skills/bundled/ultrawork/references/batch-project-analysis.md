# Batch Project Analysis — Session-Proven Patterns

Proven in session 20260504: analyzed 13 projects across 4 waves in ~6 minutes total wall clock.

## Delegate Task Context Template

The `context` field is the most important part of each task. Structure it:

```
Project root: /path/to/project/

Read [FILE1] and [FILE2] first. Check [DIRECTORY] for structure.
Look for [SPECIFIC SIGNS].
Understand what this project does end-to-end.
```

Real example that worked:
```
Project root: /home/nasbombz/Documents/Projects/Engineering-Hub/

Read Agents.md and DEPLOYMENT.md first. Check firebase.json.
Look at functions/ dir. Understand what this project does.
```

## Batch Size

- **Maximum per delegate_task call:** 3 tasks (default `max_concurrent_children`)
- **Workflow:** Wave 1 → collect results → Wave 2 → collect → Wave 3 → …
- Do NOT try to squeeze more than 3 — subagents block each other

## Output Format Request

Each subagent automatically produces a summary. The pattern is:
1. What they did (files read, commands run)
2. What the project is (1-paragraph description)
3. Tech stack table
4. Stage of development
5. Key files examined
6. Issues encountered

No need to specify output format — the default summarization is good.

## Scoping Before Batching

Before the first wave, run this to understand what you're dealing with:

```
# List all project directories and their top-level contents
for dir in ~/Documents/Projects/*/; do
  echo "=== $(basename $dir) ==="
  ls -1 "$dir" | head -15
  echo
done
```

This gives you a complete inventory so you can:
- Batch related projects together (e.g., all ML projects in one wave)
- Know the rough size of each project
- Identify obvious dust/empty projects

## Skill Recovery Pattern

When a user says a skill exists but `skills_list` doesn't show it:

1. Search for `skills-lock.json` in the current project directory
2. Read it to find the GitHub source and path
3. Fetch the raw SKILL.md from `raw.githubusercontent.com`
4. Install via `skill_manage(action='create')`

```
skills-lock.json format:
{
  "skills": {
    "ultrawork": {
      "source": "yeachan-heo/oh-my-claudecode",
      "skillPath": "skills/ultrawork/SKILL.md"
    }
  }
}

Fetch URL:
https://raw.githubusercontent.com/yeachan-heo/oh-my-claudecode/main/skills/ultrawork/SKILL.md

Create globally:
skill_manage(action='create', name='ultrawork', category='software-development', content='<SKILL.md contents>')
```
