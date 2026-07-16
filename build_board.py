# -*- coding: utf-8 -*-
"""Inject data/board_data.json into board_template.html -> board.html"""
import json, os

import glob

HERE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(HERE, "data", "board_data.json"), encoding="utf-8"))

# attach all simplified-page results, keyed "page_method_pct"
data["simplified"] = {}
for fp in glob.glob(os.path.join(HERE, "data", "simplified", "*.json")):
    r = json.load(open(fp, encoding="utf-8"))
    data["simplified"][f'{r["page"]}_{r["method"]}_{r["pct"]}'] = r
tpl = open(os.path.join(HERE, "board_template.html"), encoding="utf-8").read()
html = tpl.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
out = os.path.join(HERE, "board.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("written", out, len(html), "bytes")
