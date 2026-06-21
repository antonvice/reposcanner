# Codex Prompt: Repository Description

You are inside a software repository. Inspect the repository and write a concise customer-facing description of what this repository is.

Requirements:
- Output only one paragraph, 2-4 sentences.
- Describe the product/domain, main user-facing purpose, and the most important technical components.
- Mention the primary programming language/frameworks only if they are actually central to the repo.
- Do not mention suppliers, sampling, metadata, audits, pass/fail status, or internal delivery process.
- Do not make claims you cannot verify from the repository files.
- Prefer concrete nouns over vague phrases like "various features" or "robust solution."

Suggested process:
1. Read README files, package manifests, app/config entrypoints, and top-level directories.
2. Identify what the repo builds or operates.
3. Check the dominant language/framework from the codebase.
4. Produce only the final paragraph.

Final answer format:

<description paragraph>
