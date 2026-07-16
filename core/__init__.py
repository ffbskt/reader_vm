# -*- coding: utf-8 -*-
"""
core — the engine of the reading platform, pure domain logic:

  core.vocab     folded lookup with Spanish morphology, page vocabularies,
                 the 4 PDF vocabulary modes
  core.pipeline  known-vocab sources, target books, coverage stats,
                 token-based levels 0/25/50/75, cached leveled simplify,
                 resume-safe translate jobs, gap-fill dictionary

No HTTP, no UI. server.py wraps it today; the FastAPI backend wraps it next
(docs/ARCHITECTURE.md). Legacy Celestina modules (analyze, simplify_page)
still live at the repo root and are imported from here.
"""
from core.vocab import (fold, lookup, load_dictionary, page_new_words,
                        new_state, MODES)
from core import pipeline
