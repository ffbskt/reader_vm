# -*- coding: utf-8 -*-
"""convbot pure logic: parsing, code assignment, rendering (no network)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from convbot import anonymize as A

EXPORT = json.dumps({"name": "Chat", "type": "personal_chat", "id": 1,
    "messages": [
        {"id": 1, "type": "service", "action": "create"},
        {"id": 2, "type": "message", "date": "2026-06-07T15:30:40",
         "from": "Ann Tutaraytis", "text": "hi Denis"},
        {"id": 3, "type": "message", "date": "2026-06-07T15:31:00",
         "from": "Denis Volkonskiy",
         "text": ["hey ", {"type": "link", "text": "site.com"}, " see you"]},
        {"id": 4, "type": "message", "date": "2026-06-07T15:32:00",
         "from": "Ann Tutaraytis", "text": ""},
    ]}).encode()


def test_parse_drops_service_and_empty():
    p = A.parse_export(EXPORT)
    assert p["chat_name"] == "Chat"
    assert len(p["messages"]) == 2                 # service + empty dropped
    assert p["messages"][0]["sender"] == "Ann Tutaraytis"
    assert p["messages"][0]["date"] == "2026-06-07 15:30:40"


def test_flatten_list_text():
    assert A.flatten_text(["hey ", {"type": "link", "text": "x"}, "!"]) \
        == "hey x!"
    assert A.flatten_text("plain") == "plain"


def test_sender_codes_unique_and_collision():
    msgs = [{"sender": "Ann Tutaraytis"}, {"sender": "Denis Volkonskiy"},
            {"sender": "Anna Ivanova"}]
    codes, taken = A.code_map_for_senders(msgs)
    assert codes["Ann Tutaraytis"] == "A"      # single letter preferred
    assert codes["Denis Volkonskiy"] == "D"
    assert codes["Anna Ivanova"] == "AI"       # 2 letters only on collision
    assert len(set(codes.values())) == 3       # all distinct


def test_assign_code_avoids_taken():
    taken = {"A"}
    c = A.assign_code("Alex", taken)
    assert c != "A" and c not in {"A"} or c == "AL"
    assert c in taken


def test_normalize_codes_pure_letters_substring_safe():
    txt = "[t] D: был в K1 и K12, встретил P5"
    m = {"Denis": "D", "Ann": "A", "Батуми": "K1", "Тбилиси": "K12",
         "Фрейд": "P5"}
    nt, nm = A.normalize_codes(txt, m, keep={"D", "A"})
    assert all(c.isalpha() for c in nm.values())          # no digits
    assert len(set(nm.values())) == len(nm)               # unique
    assert nm["Denis"] == "D" and nm["Ann"] == "A"        # senders kept
    assert "K1" not in nt and "K12" not in nt             # both replaced
    assert f"{nm['Батуми']} и {nm['Тбилиси']}" in nt      # order preserved


def test_render_txt_and_map():
    p = A.parse_export(EXPORT)
    sc, _ = A.code_map_for_senders(p["messages"])
    txt = A.render_txt("Chat", p["messages"], sc)
    assert "] " in txt and ": hi Denis" in txt
    assert sc["Ann Tutaraytis"] + ":" in txt
    m = A.render_map({"Ann Tutaraytis": "AT", "Denis Volkonskiy": "DV"})
    assert "AT = Ann Tutaraytis" in m
