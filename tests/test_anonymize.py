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


def test_finalize_typed_labels_substring_safe():
    txt = "[t] D: был в K1 и K12, встретил P5"
    ent = {"Denis": {"code": "D", "type": "person"},
           "Ann": {"code": "A", "type": "person"},
           "Батуми": {"code": "K1", "type": "city"},
           "Тбилиси": {"code": "K12", "type": "city"},
           "Фрейд": {"code": "P5", "type": "person"}}
    nt, labels = A.finalize(txt, ent, reserved={"D", "A"})
    assert "K1" not in nt and "K12" not in nt             # raw codes gone
    assert labels["Батуми"].startswith("city ")          # typed place
    assert labels["Фрейд"].isalpha()                     # person = bare letter
    assert labels["Denis"] == "D"                        # sender preserved
    # substring safety: Батуми(K1) and Тбилиси(K12) got distinct labels
    assert labels["Батуми"] != labels["Тбилиси"]


def test_time_shift():
    assert A.shift_time("2026-06-07 15:30:40") == "2026-06-14 15:33:40"
    assert A.shift_time("bad") == "bad"


def test_entity_label():
    assert A.entity_label("person", "V") == "V"
    assert A.entity_label("city", "V") == "city V"
    assert A.entity_label("bar", "O") == "bar O"


def test_render_txt_and_map():
    p = A.parse_export(EXPORT)
    sc, _ = A.code_map_for_senders(p["messages"])
    txt = A.render_txt("Chat", p["messages"], sc)
    assert "] " in txt and ": hi Denis" in txt
    assert sc["Ann Tutaraytis"] + ":" in txt
    m = A.render_map({"Ann Tutaraytis": "AT", "Denis Volkonskiy": "DV"})
    assert "AT = Ann Tutaraytis" in m
