# -*- coding: utf-8 -*-
"""Extract text from a PDF into a page-marked .txt: extract_text.py <pdf> <out.txt>"""
import sys, io, os
from pypdf import PdfReader

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

pdf_path, out_path = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

reader = PdfReader(pdf_path)
print("pages:", len(reader.pages))

with open(out_path, "w", encoding="utf-8") as f:
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        f.write(f"\n<<<PAGE {i+1}>>>\n")
        f.write(text)
print("written to", out_path)
