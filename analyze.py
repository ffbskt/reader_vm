# -*- coding: utf-8 -*-
"""
Vocabulary coverage analysis:
  Book 1 = Easy Spanish step-by-step (known simple words)
  Book 2 = La Celestina (classic literature, OCR'd scan)

Outputs data/board_data.json for the HTML dashboard.

Pairing scores:
  - local mode (default): difflib ratio + shared-prefix stem heuristic
  - gemini mode: set GEMINI_API_KEY, run with --gemini to re-score the top
    candidate pairs with the Gemini API (semantic relatedness 0-100).
"""
import sys, io, os, re, json, random, difflib, argparse, time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BOOK1 = os.path.join(DATA, "book_full.txt")     # Easy Spanish (bilingual textbook)
BOOK2 = os.path.join(DATA, "celestina.txt")     # La Celestina (OCR)
BOOK2_FIRST_PAGE = 38                            # skip bibliography front matter

# --------------------------------------------------------------------------
# 1. Load pages
# --------------------------------------------------------------------------
def load_pages(path):
    raw = open(path, encoding="utf-8").read()
    parts = re.split(r"<<<PAGE (\d+)>>>", raw)
    pages = {}
    for i in range(1, len(parts), 2):
        pages[int(parts[i])] = parts[i + 1]
    return pages

# --------------------------------------------------------------------------
# 2. OCR cleanup for the Celestina scan
# --------------------------------------------------------------------------
OCR_FIXES_PATH = os.path.join(DATA, "ocr_fixes.json")

# manual fixes: OCR reads '¡' as 'j' (¡Oh -> jOh), plus frequent multi-error forms
MANUAL_FIXES = {
    "joh": "oh", "jay": "ay", "jah": "ah", "jea": "ea",
    "sf": "si", "mf": "mi",
    "compafifa": "compañía", "compaififa": "compañía",
    "compajfifa": "compañía", "compafiia": "compañía",
    "majfiana": "mañana",
}

# real words where vowel-fi-vowel is legitimate (fiar/rufián family)
FI_REAL = {"rufian", "rufianes", "desafio", "desafios", "confia", "confio",
           "porfia", "porfias", "fia", "fio"}

# OCR reads 'ñ' as 'fi'. In real Spanish 'fi' virtually always has a consonant
# on one side (fiel, beneficio, confianza); between vowels it is the ñ error
# (sefiora, dafio, engafio).
NFI_RE = re.compile(r"(?<=[aeiou])fi(?=[aeiou])")

def build_ocr_fixes(counts, trusted_display, known):
    """
    OCR repair map for Book 2 tokens. The vowel-fi-vowel -> ñ rule applies on
    its own signature; the other confusions (d->á, f->í, e<->ó) are accepted
    ONLY if the corrected form is attested in Book 1 or in the properly
    accented Book 2 vocabulary.
    """
    fixes = dict(MANUAL_FIXES)
    for w in counts:
        if fold(w) in known or w in fixes:
            continue

        # strip OCR'd '¡' before a consonant: jsefior -> sefior
        base = w
        if len(w) > 3 and w[0] == "j" and w[1] not in "aeiouáéíóú":
            base = w[1:]

        # ñ rule, unconditional on its signature
        if base not in FI_REAL and NFI_RE.search(base):
            fixes[w] = NFI_RE.sub("ñ", base)
            continue
        if base != w and fold(base) in known:
            fixes[w] = fold(base)
            continue

        # verified rules
        cands = []
        if len(w) >= 3:
            for i, ch in enumerate(w):
                if ch == "d":
                    cands.append(w[:i] + "a" + w[i + 1:])   # mds->mas, acd->aca
                if ch == "f":
                    cands.append(w[:i] + "i" + w[i + 1:])   # ofrte->oirte
        if any(ch in ES_CHARS for ch in w):                  # accent misread: cémo->como
            f = fold(w)
            for i, ch in enumerate(f):
                if ch == "e":
                    cands.append(f[:i] + "o" + f[i + 1:])
                if ch == "o":
                    cands.append(f[:i] + "e" + f[i + 1:])
        for cand in cands:
            fc = fold(cand)
            if fc in known:
                fixes[w] = fc                                # book1 spelling
                break
            td = trusted_display.get(fc)
            if td and td != w and counts.get(td, 0) >= 3:
                fixes[w] = td
                break
    return fixes

def apply_ocr_fixes(text, fixes):
    if not fixes:
        return text
    pat = re.compile(
        r"\b(" + "|".join(map(re.escape, sorted(fixes, key=len, reverse=True)))
        + r")\b", re.IGNORECASE)
    def rep(m):
        w = m.group(0)
        f = fixes.get(w.lower())
        if f is None:
            return w
        return f.capitalize() if w[0].isupper() else f
    return pat.sub(rep, text)

def clean_ocr(text):
    # join words hyphenated across line breaks: "re- \nsabio" -> "resabio"
    text = re.sub(r"([a-záéíóúñü])- *\n *([a-záéíóúñü])", r"\1\2", text)
    # word-internal digit OCR errors: rinc6n->rincón, Alcal4->Alcalá, c0sa->cosa
    def fix_digit(m):
        w = m.group(0)
        return (w.replace("6", "ó").replace("4", "á")
                 .replace("0", "o").replace("1", "l"))
    text = re.sub(r"(?<=[a-záéíóúñü])[6401](?=[a-záéíóúñü])",
                  lambda m: {"6": "ó", "4": "á", "0": "o", "1": "l"}[m.group(0)], text)
    return text

# --------------------------------------------------------------------------
# 3. Tokenize Spanish words. Book 1 mixes English explanations with Spanish,
#    so tokens that are common English words are dropped from BOTH books
#    (consistency keeps the coverage comparison fair).
# --------------------------------------------------------------------------
# Words: Latin (with the common accents of es/fr/it/de/pt) OR Cyrillic
# (Russian, incl. ё). Apostrophes inside a word are kept (l', dell', it's).
WORD_RE = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿА-Яа-яЁё]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿА-Яа-яЁё]+)*")
ES_CHARS = set("áéíóúñü")
# any non-ASCII letter marks a word as "content" in its own alphabet
NON_ASCII_LETTER = re.compile(r"[À-ÖØ-öø-ÿА-Яа-яЁё]")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")

# Book 1's PDF extraction dropped all diacritics (que, manana, espanol), the
# Celestina OCR kept them -> match Spanish on accent-folded forms. Cyrillic
# is left as-is (just lower-cased); ё -> е so the two spellings unify.
FOLD = str.maketrans("áéíóúñüёЁ", "aeiounuее")

def fold(w):
    return w.translate(FOLD).lower()

EN_COMMON = set("""
the of and to in is that it you for on with as are this be or at from by an
was were will would can could should may might must have has had do does did
not but if then than so all any each which who whom whose what when
where why how there here they them their she his her its we our your my
i me him us out up down over about into after before between during
above below again further once more most other some such only own same
too very just also both few many much less least verb noun adjective
pronoun tense plural singular feminine masculine chapter exercise answer
answers example examples translate translation english spanish word words
sentence sentences page following complete correct form forms used using
meaning means pronounced pronunciation practice practise letter letters
subject object preposition conjugation conjugate conjugated ending endings
stem irregular regular reflexive indicative subjunctive imperative
infinitive gerund participle preterite imperfect future conditional present
past number gender article articles definite indefinite key reading exercises
vocabulary grammar step easy read write speak listen say tell make take
""".split())

# broader English lexicon for word-level language classification (book 1 is a
# bilingual textbook; these must not leak into the Spanish vocabulary)
EN_WORDS = EN_COMMON | set("""
about above accept across act add afraid after afternoon again age ago agree
agreeable air all almost alone along already always among amount angry animal
another answer any anyone anything appear apple april area arm army around
arrive art ask attention august aunt autumn away baby back bad bag ball bank
be bear beautiful because become bed been beer before begin behind
believe bell belong below beside best better big bird birthday bit black blue
board boat body book born borrow boss both bottle bottom box boy bread break
breakfast bring brother brown build building bus business busy buy call came
car card care carry case cat catch cause center certain chair chance change
cheap check child children choose church city class clean clear climb clock
close clothes cloud coffee cold color come common company continue cook cool
corner cost count country course cousin cover cow cross cry cup cut dance
dark date daughter day dead dear december decide deep desk die different
dinner direction dirty dish doctor dog door double doubt dress drink drive
drop dry duck each early earn earth east eat egg eight either else end
enough enter even evening ever every everyone everything exact expect
explain eye face fact fall family famous far farm fast fat father fear
february feel few field fight fill find fine finger finish fire first fish
five floor flower fly follow food foot football force foreign forget four
free friend front fruit full fun funny game garden gave get girl give glad
glass go god gold gone good got great green ground group grow guess had
hair half hand happen happy hard hat hate head health hear heart heavy hello
help high hill history hold holiday home hope horse hospital hot hour house
however hundred hungry hurry hurt husband ice idea important inside instead
interest island january job join juice july jump june keep kill kind king
kitchen knee know lady lake land language large last late laugh lead learn
leave left leg lend lesson lie life light like line lion list little live
long look lose lot loud love low lunch machine mad mail main man many map
march market marry match matter mean meat meet member men middle milk mind
minute miss moment monday money month mood moon morning mother mountain
mouth move movie music name near necessary need never new news next nice
night nine nobody noise none noon north nose note nothing november now
nurse ocean october offer office often old once one only open orange order
other ought outside own paint pair paper parent park part party pass paste
pay peace pen pencil people perhaps person pick picture piece place plan
plane plant play please point police poor possible pour power prepare
pretty price probably problem pull push put queen question quick quiet
quite rain reach ready real really reason receive red remember rest return
rice rich ride right ring rise river road rock room round rule run sad
safe salt same sand saturday save saw school sea season seat second see
seem sell send september serious seven several shall shape share sharp
ship shirt shoe shop short show sick side sign simple since sing sister
sit six size skin sky sleep slow small smell smile snow sock soft some
someone something sometimes son song soon sound soup south space spend
sport spring stand star start station stay still stone stop store story
straight strange street strong student study sugar summer sun sunday
supper sure surprise sweet swim table tail talk tall taste tea teach
teacher team television ten test thank thing think third thirsty though
thought thousand three through throw thursday ticket time tired today
together tomorrow tonight top touch town train travel tree trip trouble
true try tuesday turn twice two uncle under understand university until
use usually vacation view village visit voice wait wake walk wall want
warm wash watch water way weak wear weather wednesday week well west wet
wheel while white wide wife win wind window wine winter wish woman women
wonder wood work world worry write wrong year yellow yes yesterday yet
young zero ally moods according bank
""".split())

# Spanish function words that overlap with English spellings ("no", "me", "a")
ES_STOP = set("""
el la los las un una unos unas y o u de del a al en que qué no si sí me te
se nos os le les lo mi tu su es son era eran fue fueron ser estar esta está
están este esto estos esas ese esa esos aquellos con por para como cómo pero
más mas muy ya cuando dónde donde quién quien porque también hay he ha han
hemos había todo toda todos todas otro otra nada algo alguien nadie yo tú él
ella ellos ellas usted ustedes nosotros vosotros mío tuyo suyo cada sin sobre
entre hasta desde ni bien mal ahora aquí allí así pues entonces
""".split())

def classify_language(w, es_evidence=frozenset()):
    """Word-level language call (en/es/ru) for filtering vocabulary."""
    if CYRILLIC.search(w):
        return "ru"
    if any(c in ES_CHARS for c in w):
        return "es"
    if w in ES_STOP:
        return "es"
    if fold(w) in es_evidence:          # attested in the pure-Spanish corpus
        return "es"
    if w in EN_WORDS or (w.endswith("s") and w[:-1] in EN_WORDS):
        return "en"
    return "es"                          # default to the target language

EN_STOP_CORE = set("the of and to is that you are was were with for this not".split())

def language_profile(tokens):
    """Rough share of English vs Spanish running text, by stopword hits."""
    en = sum(1 for w in tokens if w in EN_STOP_CORE)
    es = sum(1 for w in tokens if w in ES_STOP)
    total = max(en + es, 1)
    return {"en": round(en / total * 100, 1), "es": round(es / total * 100, 1)}

def is_counted(w):
    if len(w) < 2:
        return False
    # a word in its own script (accented Latin or Cyrillic) is content;
    # only plain-ASCII English function words are dropped
    if NON_ASCII_LETTER.search(w):
        return True
    return w not in EN_COMMON

def proper_nouns(text, min_count=3, ratio=0.85):
    """Words whose occurrences are (almost) always capitalized -> names."""
    lower = Counter()
    capital = Counter()
    for w in WORD_RE.findall(text):
        lw = w.lower()
        if w[0].isupper():
            capital[lw] += 1
        else:
            lower[lw] += 1
    out = set()
    for w, c in capital.items():
        total = c + lower[w]
        if total >= min_count and c / total >= ratio and len(w) > 2:
            out.add(w)
    return out

def tokenize(text):
    return [w.lower() for w in WORD_RE.findall(text)]

def counted_words(text):
    return [w for w in tokenize(text) if is_counted(w)]

# --------------------------------------------------------------------------
# 4. Sentence extraction for Book 2 examples
# --------------------------------------------------------------------------
SPEAKER_RE = re.compile(r"^[A-ZÁÉÍÓÚÑ]+\.\s*[—-]\s*")

def sentences_from(text):
    t = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.!?…]) +", t)
    out = []
    for s in parts:
        s = SPEAKER_RE.sub("", s.strip())          # drop PARMENO.— prefixes
        s = re.sub(r"^\W+", "", s)
        words = tokenize(s)
        if len(words) < 5 or len(words) > 35:
            continue
        if sum(c.isdigit() for c in s) > 2:
            continue
        out.append(s)
    return out

# --------------------------------------------------------------------------
# 5. Local pairing score
# --------------------------------------------------------------------------
def local_pair_score(unknown, known):
    """0-100: string similarity boosted when they share a long prefix (stem)."""
    ratio = difflib.SequenceMatcher(None, unknown, known).ratio()
    pl = 0
    for a, b in zip(unknown, known):
        if a != b:
            break
        pl += 1
    stem_bonus = min(pl / max(len(unknown), 1), 1.0) * 0.35
    return round(min(ratio + stem_bonus, 1.0) * 100)

def best_pairs(unknown_words, known_list, top_n=3, fold=lambda w: w):
    pairs = {}
    for uw in unknown_words:
        cands = difflib.get_close_matches(fold(uw), known_list, n=top_n, cutoff=0.6)
        scored = [{"known": c, "score": local_pair_score(fold(uw), c), "source": "local"}
                  for c in cands]
        scored.sort(key=lambda x: -x["score"])
        pairs[uw] = scored
    return pairs

# --------------------------------------------------------------------------
# 6. Gemini re-scoring (optional)
# --------------------------------------------------------------------------
GEMINI_MODEL = "gemini-flash-lite-latest"

def read_api_key():
    """GEMINI_API_KEY / GOOGLE_API_KEY env var, or a key file next to this script."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        for name in ("gemini_key.txt", "API_KEY.txt"):
            kf = os.path.join(HERE, name)
            if os.path.exists(kf):
                key = open(kf, encoding="utf-8-sig").read().strip()
                break
    return key

def gemini_rescore(pairs, limit=100):
    import requests
    key = read_api_key()
    if not key:
        print("!! GEMINI_API_KEY not set - skipping Gemini re-scoring")
        return pairs, False
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={key}")
    items = [(uw, c["known"]) for uw, p in pairs.items() for c in p[:1]][:limit]
    batch = 25
    done = 0
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        listing = "\n".join(f"{a} | {b}" for a, b in chunk)
        prompt = (
            "You are scoring Spanish word pairs from an OCR'd classic text vs a "
            "learner vocabulary. For each pair 'A | B', give a 0-100 score: 100 = "
            "same lemma or inflection of the same word (or A is an OCR error of B), "
            "70-99 = same word family or near-synonym, 30-69 = related meaning, "
            "0-29 = unrelated (just looks similar). Reply ONLY with JSON: "
            '[{"a":"...","b":"...","score":N,"relation":"short note"}]\n\n' + listing
        )
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1}}
        try:
            r = requests.post(url, json=body, timeout=60)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
            for row in json.loads(text):
                a = str(row.get("a", "")).lower()
                for p in pairs.get(a, []):
                    if p["known"] == str(row.get("b", "")).lower():
                        p["score"] = int(row.get("score", p["score"]))
                        p["source"] = "gemini"
                        p["relation"] = row.get("relation", "")
            done += len(chunk)
            time.sleep(2)  # stay under free-tier rate limits
        except Exception as e:
            print("!! Gemini batch failed:", e)
            break
    print(f"Gemini re-scored {done} pairs")
    return pairs, done > 0

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gemini", action="store_true", help="re-score pairs with Gemini API")
    args = ap.parse_args()

    p1 = load_pages(BOOK1)
    p2 = load_pages(BOOK2)
    book1_text = "\n".join(p1[k] for k in sorted(p1))
    book2_text = clean_ocr("\n".join(p2[k] for k in sorted(p2) if k >= BOOK2_FIRST_PAGE))
    print(f"book1 pages={len(p1)}  book2 pages used={sum(1 for k in p2 if k >= BOOK2_FIRST_PAGE)}")

    b1_counts = Counter(counted_words(book1_text))
    prelim = Counter(counted_words(book2_text))

    # language detection: profile each book, pick the shared language,
    # default = dominant language of book 2
    prof1 = language_profile(tokenize(book1_text))
    prof2 = language_profile(tokenize(book2_text))
    print(f"book1 language profile: {prof1}   book2: {prof2}")
    b2_evidence = frozenset(fold(w) for w in prelim)
    b1_lang = {w: classify_language(w, b2_evidence) for w in b1_counts}
    dropped_en = sorted(w for w, l in b1_lang.items() if l == "en" and b1_counts[w] >= 2)

    # known set: Spanish-classified words only, accent-folded
    # (book1 lost its diacritics in extraction anyway)
    known = {fold(w) for w, c in b1_counts.items()
             if c >= 2 and b1_lang[w] == "es"}

    # extra allowed words the user chose to learn (add_frequent.py appends here)
    extra_path = os.path.join(DATA, "extra_known.txt")
    extra = set()
    if os.path.exists(extra_path):
        for line in open(extra_path, encoding="utf-8"):
            w = line.split("#")[0].strip().lower()
            if w:
                extra.add(fold(w))
        print(f"extra known words from extra_known.txt: {len(extra)}")
    known |= extra
    known_list = sorted(known)

    # OCR repair of book 2: build verified fix map, rewrite text, recount
    trusted_display = {}
    for w, c in prelim.items():
        if any(ch in ES_CHARS for ch in w):
            f = fold(w)
            if f not in trusted_display or c > prelim[trusted_display[f]]:
                trusted_display[f] = w
    fixes = build_ocr_fixes(prelim, trusted_display, known)
    with open(OCR_FIXES_PATH, "w", encoding="utf-8") as f:
        json.dump(fixes, f, ensure_ascii=False, indent=1)
    sample = list(fixes.items())[:8]
    print(f"ocr fixes: {len(fixes)} words repaired, e.g. {sample}")
    book2_text = apply_ocr_fixes(book2_text, fixes)
    b2_counts = Counter(counted_words(book2_text))
    names = proper_nouns(book2_text)
    print(f"book1 vocab (freq>=2, es only): {len(known)}"
          f"   dropped english words: {len(dropped_en)}"
          f"   book2 vocab: {len(b2_counts)}   proper nouns in book2: {len(names)}")

    def is_known(w):
        return fold(w) in known

    b2_vocab = {w for w in b2_counts if w not in names}
    covered = {w for w in b2_vocab if is_known(w)}
    unknown = b2_vocab - covered
    tok_total = sum(c for w, c in b2_counts.items() if w not in names)
    tok_known = sum(c for w, c in b2_counts.items() if w not in names and is_known(w))
    print(f"type coverage: {len(covered)}/{len(b2_vocab)} = {len(covered)/len(b2_vocab):.1%}")
    print(f"token coverage: {tok_known}/{tok_total} = {tok_known/tok_total:.1%}")

    unknown_by_freq = sorted(unknown, key=lambda w: -b2_counts[w])
    pair_targets = [w for w in unknown_by_freq if b2_counts[w] >= 3][:150]
    print(f"pairing {len(pair_targets)} frequent unknown words...")
    pairs = best_pairs(pair_targets, known_list, fold=fold)

    gemini_used = False
    if args.gemini:
        pairs, gemini_used = gemini_rescore(pairs)

    sents = sentences_from(book2_text)
    random.seed(42)
    random.shuffle(sents)
    examples = []
    for s in sents:
        ws = counted_words(s)
        if not ws:
            continue
        unk = sorted({w for w in ws if w in unknown})
        cov = 1 - len([w for w in ws if w in unknown]) / len(ws)
        examples.append({"text": s, "unknown": unk, "coverage": round(cov * 100)})
        if len(examples) >= 120:
            break
    # mix: mostly sentences with unknown words, sorted hardest-first, few clean ones
    examples.sort(key=lambda e: (len(e["unknown"]) == 0, -len(e["unknown"])))
    examples = examples[:60]

    data = {
        "meta": {
            "book1": "Easy Spanish step-by-step (learner vocabulary, freq>=2)",
            "book2": "La Celestina, F. de Rojas (OCR scan, pages %d+)" % BOOK2_FIRST_PAGE,
            "book1_vocab": len(known),
            "book2_vocab": len(b2_vocab),
            "type_coverage": round(len(covered) / len(b2_vocab) * 100, 1),
            "token_coverage": round(tok_known / tok_total * 100, 1),
            "gemini_used": gemini_used,
            "scorer": "gemini-2.0-flash" if gemini_used else "local (difflib + stem)",
            "languages": [
                {"code": "es", "name": "Español",
                 "book1_pct": prof1["es"], "book2_pct": prof2["es"],
                 "common": True, "default": True},
                {"code": "en", "name": "English",
                 "book1_pct": prof1["en"], "book2_pct": prof2["en"],
                 "common": prof2["en"] >= 5, "default": False},
            ],
            "dropped_english": len(dropped_en),
            "extra_known": len(extra),
        },
        "book1_words": sorted(
            [{"w": fold(w), "n": c} for w, c in b1_counts.items()
             if c >= 2 and b1_lang[w] == "es"]
            + [{"w": w, "n": b2_counts.get(w, 0), "extra": True}
               for w in extra
               if w not in {fold(x) for x, c in b1_counts.items()
                            if c >= 2 and b1_lang[x] == "es"}],
            key=lambda x: x["w"]),
        "book2_words": [{"w": w, "n": b2_counts[w], "known": is_known(w)}
                        for w in sorted(b2_vocab)],
        "pairs": [
            {"unknown": uw, "freq": b2_counts[uw], "candidates": pairs[uw]}
            for uw in pair_targets
        ],
        "examples": examples,
        "proper_nouns": sorted(names),
    }
    out = os.path.join(DATA, "board_data.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("written", out)

if __name__ == "__main__":
    main()
