# -*- coding: utf-8 -*-
"""
Per-user limits (free tier). Storage lives in core.pipeline.STORAGE_LIMIT;
these are the API-level abuse/cost guards. Overridable by env for a future
paid tier.
"""
import os

# non-cached pages a user may translate per day (each = 1 Gemini call)
DAILY_PAGES = int(os.environ.get("LIMIT_DAILY_PAGES", "100"))
# translations running at once per user
MAX_CONCURRENT_JOBS = int(os.environ.get("LIMIT_CONCURRENT_JOBS", "1"))
# pages a single translate request may span (cheap guard against huge ranges)
MAX_RANGE = int(os.environ.get("LIMIT_MAX_RANGE", "200"))
# messages a user may anonymize per day (convbot); each ~= a slice of a
# Gemini call, same cost model as a translated page
DAILY_ANON_MESSAGES = int(os.environ.get("LIMIT_DAILY_ANON", "2000"))
