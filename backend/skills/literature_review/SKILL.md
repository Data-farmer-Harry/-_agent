---
id: literature_review
name: 材料文献检索与综述
description: Search, screen, and summarize materials literature candidates without overstating citation certainty.
triggers:
  - 查文献
  - 找文献
  - 文献综述
  - 论文
  - DOI
  - citation
  - literature
  - paper
  - papers
preferred_tools:
  - literature.search
  - file.read
---

When this skill is active, treat literature search results as candidate metadata, not verified full-text evidence.

Use a source-quality hierarchy:

1. DOI / publisher metadata and official pages.
2. Peer-reviewed journal or conference records.
3. Preprints when the user wants breadth or recent work.
4. Secondary summaries only as leads, not final evidence.

For each result, prefer reporting title, authors, year, venue, DOI/link, and why it may be relevant. Avoid inventing abstracts, page numbers, claims, or citation counts beyond what the tool returned.

If the user asks for a research direction or related work section, separate:

Known evidence → likely relevance → missing verification.
