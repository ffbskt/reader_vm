# -*- coding: utf-8 -*-
"""Compatibility shim — the code moved to core/vocab.py (roadmap 1.2)."""
from core.vocab import *                                    # noqa: F401,F403
from core.vocab import fold, lookup, load_dictionary, page_new_words, \
    new_state, MODES, CLITICS, VERB_ENDS                    # noqa: F401
