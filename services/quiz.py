import re
from docx import Document


def extract_docx(path):
    doc = Document(path)
    lines = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if text:
            lines.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            lines.append(" | ".join(c for c in cells if c))
    return lines


ANSWER_MAP = {letter: i for i, letter in enumerate("ABCDEF")}
Q_RE = re.compile(r"^\s*(savol|question|вопрос|вопрос)\s*[:.\-)]*\s*", re.I)
NUM_RE = re.compile(r"^\s*\d+\s*[.)]\s*")
OPT_RE = re.compile(r"^\s*[A-Fa-f]\s*[).:]\s*")
ANS_RE = re.compile(
    r"(?:javob|answer|ответ|to'?\.?g'?ri|togri)\s*[:.\-)]?\s*\**\s*([a-fA-F])(?=\s*[)\s.]|$)",
    re.I)


def parse_questions(lines) -> list[dict]:
    qs = []
    cur_q = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        ans_match = ANS_RE.search(s)
        if ans_match:
            cur_q["answer"] = ANSWER_MAP.get(ans_match.group(1).upper())
            continue
        opt = OPT_RE.match(s)
        if cur_q and opt:
            cur_q["options"].append(re.sub(r"^[A-Fa-f]\s*[).:]\s*", "", s).strip())
            continue
        if Q_RE.match(s) or NUM_RE.match(s) or not (cur_q and cur_q["options"]):
            if cur_q and cur_q["q"] and cur_q["options"]:
                qs.append(cur_q)
            cur_q = {"q": Q_RE.sub("", s, count=1).strip(), "options": [], "answer": None}
            cur_q["q"] = NUM_RE.sub("", cur_q["q"], count=1).strip()
        elif cur_q:
            cur_q["q"] += " " + s
    if cur_q and cur_q["q"] and cur_q["options"]:
        qs.append(cur_q)
    return [q for q in qs if len(q["options"]) >= 2]


def letters_for(n):
    return ["A", "B", "C", "D", "E", "F"][:n]