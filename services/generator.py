import json
import re
import time

from google import genai
from google.genai import types

import config
from config import GEMINI_API_KEY, GEMINI_MODEL, log

client = (
    genai.Client(
        api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT * 1000)
    )
    if GEMINI_API_KEY
    else None
)

LANG_NAMES = {"uz": "oʻzbek lotin alifbosidagi", "ru": "русский", "en": "English"}

REFERAT_PROMPT = """Sen oliy taʼlim muassasasi talabasi uchun professional referat yozasan.

MUHIM: Siz FAQAT "{topic}" mavzusida yozasiz. Boshqa mavzu, umumiy yoki aloqasiz matn yozish qatʼiy TAQIQLANADI.
Til: {lang}
Hajm talablari (qatʼiy bajar):
- Jami matn {words} soʼz atrofida boʼlsin (sezilarli oshmasin ham, kam boʼlmasin ham)
- {sections} ta boʼlim boʼlsin
- Har bir boʼlim {words_per_section} soʼz atrofida, 2-4 ta paragrafdan iborat boʼlsin
Boʼlimlar tartibi: kirish, asosiy qism (mavzuga qarab 2-5 boʼlim), xulosa, adabiyotlar roʼyxati.
Matn quruq boʼlmasin, misollar va tahlil boʼlsin, nusxalashga oʻxshamasin, tabiiy akademik uslubda yozilsin.
ADABIYOTLAR: faqat shu mavzu boʼyicha haqiqatda mavjud boʼlgan manbalarni koʼrsating.
Muallif nomini, sarlavhani yoki yilni oʼylab topmang — ishonchingiz komil boʼlmagan "manba"ni yozmang.
Haqiqiy manbalar roʼyxatini topa olmasangiz, koʼrsatilgan manbalar "taxminiy, tekshirish talab qilinadi"
deb izohlangan holda yozing.
Javobni FAQAT sof JSON koʻrinishida qaytar:
{{
  "title": "mavzu sarlavhasi",
  "sections": [
    {{"heading": "boʼlim nomi", "paragraphs": ["matn...", "matn..."]}}
  ]
}}
Boshqa hech narsa yozma."""

PPTX_PROMPT = """Sen professional prezentatsiya muallifisan.

MUHIM: Siz FAQAT "{topic}" mavzusida yozasiz. Boshqa mavzuga o\'tish TAQIQLANADI.
Til: {lang}
Slaydlar soni: aynan {n} ta (muqova slayd ham shu son ichida).
Har bir slaydda: qisqa sarlavha va 3-5 ta qisqa bullet (har biri 1-2 gap).
XILMA-XILLIK (qatʼiy): hech qanday takroriyat yoʻq — bitta gap, ibora yoki misol ikki marta ishlatilmasin; bir xil fikrni boshqa soʻzlar bilan qayta yozish ham TAQIQLANADI.
Har bir slayd faqat oʻziga xos material bersin va jihatini oʻzgartirsin: taʼrif → tarix/rivojlanish → turlar → statistika va raqamlar → real misollar → afzallik → muammo va yechim → xulosa.
Bulletlar konkret boʻlsin: aniq raqamlar, faktlar, nomlar; quruq umumiy gaplar («bu juda muhim», «juda koʻp afzalliklari bor») yozilmasin.
Birinchi slayd — muqova (title + subtitle), oxirgi slayd — xulosa yoki rahmat.
Har bir slayd uchun speaker notes (nutq matni) yoz — 2-4 gap.
Har bir slaydga "image_hint" yoz — 2-4 ta inglizcha kalit soʻz (rasm qidirish uchun, masalan "artificial intelligence robot"), boshqa tilda emas.
Javobni FAQAT sof JSON koʻrinishida qaytar:
{{
  "title": "prezentatsiya sarlavhasi",
  "subtitle": "qisqa izoh",
  "slides": [
    {{"title": "slayd sarlavhasi", "bullets": ["...", "..."], "notes": "speaker notes...", "image_hint": "english keywords"}}
  ]
}}
Boshqa hech narsa yozma."""

XLSX_PROMPT = """Sen maʼlumotlar jadvalini tayyorlaydigan mutaxassissan.
Mavzu: "{topic}"
Til: {lang}
Aynan {rows} ta qator boʼlsin. Ustunlar mazmunga mos boʻlsin (2-6 ta ustun).
Maʼlumotlar real va foydali boʻlsin, taxminiy raqamlar boʻlsa "≈" belgisi bilan yozilsin.
Javobni FAQAT sof JSON koʻrinishida qaytar:
{{
  "title": "jadval sarlavhasi",
  "headers": ["Ustun 1", "Ustun 2"],
  "rows": [["qiymat", "qiymat"]],
  "note": "manba yoki izoh (ixtiyoriy, boʻsh qoldirsa ham boʼladi)"
}}
Boshqa hech narsa yozma."""


def _extract_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _ask(prompt: str) -> dict | list:
    if not client:
        raise RuntimeError("AI xizmati sozlanmagan")
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.4, max_output_tokens=24000
                ),
            )
            return _extract_json(resp.text or "")
        except Exception as exc:
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            retryable = (
                code in (408, 429, 500, 502, 503, 504)
                or isinstance(exc, (json.JSONDecodeError, TimeoutError, ConnectionError))
                or "timeout" in type(exc).__name__.lower()
            )
            if not retryable or attempt == 2:
                log.warning("AI request failed type=%s code=%s", type(exc).__name__, code)
                raise RuntimeError(
                    "AI xizmati javob bermadi. Keyinroq qayta urinib ko'ring."
                ) from exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError("AI xizmati javob bermadi")


def _ask_validated(prompt: str, check) -> dict | list:
    for attempt in range(2):
        data = _ask(
            prompt
            if not attempt
            else prompt
            + "\nOldingi javob noto'g'ri yoki to'liq emas edi. Barcha talablarni bajarib, to'liq JSON qaytar."
        )
        if check(data):
            return data
    raise RuntimeError("AI javobining shakli yoki hajmi talabga mos emas")


def _topic_ok(topic: str, data) -> bool:
    words = [w for w in re.split(r"\W+", topic.lower()) if len(w) > 3]
    if not words:
        return True
    if not isinstance(data, dict):
        return False
    # Inspect slide text too, not only a presentation's cover title.
    blob = json.dumps(data, ensure_ascii=False).lower()
    return any(w in blob for w in words)


def _ref_ok(data) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("title", ""), str)
        and isinstance(data.get("sections"), list)
        and bool(data["sections"])
        and all(
            isinstance(s, dict)
            and isinstance(s.get("heading", ""), str)
            and isinstance(s.get("paragraphs"), list)
            and bool(s["paragraphs"])
            and all(isinstance(p, str) and p.strip() for p in s["paragraphs"])
            for s in data["sections"]
        )
    )


def gen_referat(lang: str, topic: str, pages: int) -> dict:
    words = max(pages - 1, 1) * 300
    sections = min(14, max(3, pages // 2 + 1))
    prompt = REFERAT_PROMPT.format(
        topic=topic,
        lang=LANG_NAMES.get(lang, lang),
        words=words,
        sections=sections,
        words_per_section=max(50, words // sections),
    )
    data = _ask_validated(prompt, _ref_ok)
    if not _topic_ok(topic, data) or not int(words * 0.8) <= _word_count(data) <= int(words * 1.3):
        data = _ask_validated(
            prompt + f"\nMavzu va hajmni tekshir: {words} so'z atrofida bo'lsin.", _ref_ok
        )
    if (
        not _ref_ok(data)
        or not _topic_ok(topic, data)
        or not int(words * 0.8) <= _word_count(data) <= int(words * 1.3)
    ):
        raise RuntimeError("Referat hajmi yoki mavzusi talabga mos emas")
    data["include_cover"] = pages > 1
    refs = data.get("references")
    if isinstance(refs, list) and refs and all(isinstance(r, str) for r in refs):
        label = {"uz": "ADABIYOTLAR", "ru": "ЛИТЕРАТУРА", "en": "REFERENCES"}.get(
            lang, "ADABIYOTLAR"
        )
        data["sections"].append({"heading": label, "paragraphs": [r for r in refs if r.strip()]})
    return data


def _word_count(data: dict) -> int:
    return sum(
        len(str(p).split()) for s in data.get("sections", []) for p in s.get("paragraphs", [])
    )


def _ppt_ok(data) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("title", ""), str)
        and isinstance(data.get("slides"), list)
        and bool(data["slides"])
        and all(
            isinstance(s, dict)
            and isinstance(s.get("title"), str)
            and s["title"].strip()
            and isinstance(s.get("bullets", []), list)
            and len(s.get("bullets", [])) <= 6
            and all(isinstance(b, str) and 0 < len(b) <= 500 for b in s.get("bullets", []))
            and isinstance(s.get("notes", ""), str)
            for s in data["slides"]
        )
    )


def gen_pptx(lang: str, topic: str, n: int) -> dict:
    prompt = PPTX_PROMPT.format(topic=topic, lang=LANG_NAMES.get(lang, lang), n=n)
    data = _ask_validated(prompt, _ppt_ok)
    if not _topic_ok(topic, data) or len(data["slides"]) != n:
        data = _ask_validated(prompt + f"\nAynan {n} ta slayd kerak.", _ppt_ok)
    if not _ppt_ok(data) or len(data["slides"]) < n or not _topic_ok(topic, data):
        raise RuntimeError("Slaydlar hajmi yoki mavzusi talabga mos emas")
    data["slides"] = data["slides"][:n]
    return data


def _xls_ok(data) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("headers"), list)
        and 2 <= len(data["headers"]) <= 6
        and all(isinstance(h, str) and h.strip() for h in data["headers"])
        and isinstance(data.get("rows"), list)
        and all(
            isinstance(row, list)
            and len(row) == len(data["headers"])
            and all(cell is None or type(cell) in (str, int, float) for cell in row)
            for row in data["rows"]
        )
    )


def gen_xlsx(lang: str, topic: str, rows: int) -> dict:
    prompt = XLSX_PROMPT.format(topic=topic, lang=LANG_NAMES.get(lang, lang), rows=rows)
    data = _ask_validated(prompt, _xls_ok)
    if len(data.get("rows", [])) != rows:
        data = _ask_validated(prompt + f"\nAynan {rows} ta qator kerak.", _xls_ok)
    if not _xls_ok(data) or len(data["rows"]) < rows:
        raise RuntimeError("Jadval hajmi talabga mos emas")
    data["rows"] = data["rows"][:rows]
    data.setdefault("title", topic)
    return data


TEST_PROMPT = """Sen test savollari tuzuvchi mutaxassissan. Mavzu: "{topic}"
Til: {lang}
Aynan {n} ta savol tuz:
- Har savol 4 ta variant (A, B, C, D), faqat 1 tasi toʼgʼri boʼlsin
- Savollar mavzu ichining turli tomonlarini qamrab olsin, takrorlanmasin
- Javob indeksi 0 dan boshlanadi (A=0, B=1, C=2, D=3)
Javobni FAQAT sof JSON qaytar:
[
  {{"q": "savol matni", "options": ["variant A", "variant B", "variant C", "variant D"], "answer": 1}}
]
Boshqa hech narsa yozma."""

TRANSLATE_PROMPT = """Sen professional tarjimonsan. Quyidagi raqamlangan matnni tarjima qil: {target}
Har bir raqam uchun aynan BITTADAN tarjima qaytar, tartib va sonni o'zgartirma,
boshqa hech qanday belgi, ro'yxat yoki shaftoli qo'shma.
Matn:
{text}
Javobni FAQAT sof JSON qaytar: {{"paragraphs": ["1-tarjima", "2-tarjima", ...]}}
Boshqa hech narsa yozma."""

REWRITE_PROMPT = """Sen akademik matnlarni qayta yozuvchi mutaxassissan. Quyidagi raqamlangan matnni
o'zgacha so'zlar bilan, shu ma'noni to'liq saqlab, tabiiy va yaxlit qilib qayta yoz.
Har bir raqam uchun aynan BITTADAN variant qaytar, tartib va sonni o'zgartirma,
boshqa hech qanday belgi yoki rozet qo'shma.
Matn:
{text}
Javobni FAQAT sof JSON qaytar: {{"paragraphs": ["1-variant", "2-variant", ...]}}
Boshqa hech narsa yozma."""


def gen_test(lang: str, topic: str, n: int) -> list[dict]:
    def valid(data):
        return (
            isinstance(data, list)
            and len(data) >= n
            and all(
                isinstance(q, dict)
                and isinstance(q.get("q"), str)
                and q["q"].strip()
                and isinstance(q.get("options"), list)
                and len(q["options"]) == 4
                and all(isinstance(o, str) and o.strip() for o in q["options"])
                and type(q.get("answer")) is int
                and 0 <= q["answer"] < 4
                for q in data
            )
        )

    return _ask_validated(
        TEST_PROMPT.format(topic=topic, lang=LANG_NAMES.get(lang, lang), n=n), valid
    )[:n]


def gen_translate(target: str, paragraphs: list[str]) -> list[str]:
    return _transform_paragraphs(
        paragraphs, lambda text: TRANSLATE_PROMPT.format(target=target, text=text)
    )


def gen_rewrite(paragraphs: list[str]) -> list[str]:
    return _transform_paragraphs(paragraphs, lambda text: REWRITE_PROMPT.format(text=text))


PPTX_FROM_TEXT_PROMPT = """Sen berilgan matn asosida professional prezentatsiya tayyorlaysan.
Til: {lang}
Slaydlar soni: aynan {n} ta (muqova slayd ham shu son ichida, oxirgi slayd — xulosa).
Berilgan matnni bo'limlar bo'yicha taqsimlab chiq: har bir slaydda qisqa sarlavha va
3-5 ta qisqa bullet (har biri 1-2 gap) bo'lsin, matnning asosiy ma'lumotlarini saqla.
XILMA-XILLIK (qatʼiy): hech qanday takroriyat yoʻq — bitta gap, ibora yoki misol ikki marta ishlatilmasin; bir xil fikrni boshqa soʻzlar bilan qayta yozish ham TAQIQLANADI.
Har bir slayd faqat oʻziga xos material bersin va jihatini oʻzgartirsin: taʼrif → tarix/rivojlanish → turlar → statistika va raqamlar → real misollar → afzallik → muammo va yechim → xulosa.
Bulletlar konkret boʻlsin: aniq raqamlar, faktlar, nomlar; quruq umumiy gaplar («bu juda muhim», «juda koʻp afzalliklari bor») yozilmasin.
Matn:
{text}
Har bir slayd uchun speaker notes (nutq matni) yoz — 2-4 gap.
Har bir slaydga "image_hint" yoz — 2-4 ta inglizcha kalit soʻz (rasm qidirish uchun, masalan "science laboratory"), boshqa tilda emas.
Javobni FAQAT sof JSON koʻrinishida qaytar:
{{
  "title": "prezentatsiya sarlavhasi",
  "subtitle": "qisqa izoh",
  "slides": [
    {{"title": "slayd sarlavhasi", "bullets": ["...", "..."], "notes": "speaker notes...", "image_hint": "english keywords"}}
  ]
}}
Boshqa hech narsa yozma."""


def gen_ppt_from_text(lang: str, text: str, n: int) -> dict:
    if len(text) > 40000:
        raise ValueError("Word→PPT uchun matn juda katta (maksimum 40 000 belgi)")
    prompt = PPTX_FROM_TEXT_PROMPT.format(lang=LANG_NAMES.get(lang, lang), n=n, text=text)
    data = _ask_validated(prompt, lambda value: _ppt_ok(value) and len(value["slides"]) >= n)
    data["slides"] = data["slides"][:n]
    return data


def _transform_paragraphs(paragraphs: list[str], make_prompt) -> list[str]:
    if (
        not paragraphs
        or not all(isinstance(p, str) for p in paragraphs)
        or sum(map(len, paragraphs)) > config.MAX_TEXT_CHARS
    ):
        raise ValueError("Matn bo'sh yoki ruxsat etilgan hajmdan katta")
    pieces = []
    for index, paragraph in enumerate(paragraphs):
        # Long individual paragraphs are chunked too; no source text is truncated.
        for start in range(0, len(paragraph), 6000):
            part = paragraph[start : start + 6000]
            if part.strip():
                pieces.append((index, part))
    output = [[] for _ in paragraphs]
    offset = 0
    while offset < len(pieces):
        batch, size = [], 0
        while offset < len(pieces) and (not batch or size + len(pieces[offset][1]) <= 8000):
            batch.append(pieces[offset])
            size += len(pieces[offset][1])
            offset += 1
        expected = len(batch)

        def valid(data, expected=expected):
            return (
                isinstance(data, dict)
                and isinstance(data.get("paragraphs"), list)
                and len(data["paragraphs"]) == expected
                and all(isinstance(p, str) and p.strip() for p in data["paragraphs"])
            )

        text = "\n".join(f"{i + 1}. {part}" for i, (_, part) in enumerate(batch))
        data = _ask_validated(make_prompt(text), valid)
        for (index, _), translated in zip(batch, data["paragraphs"], strict=True):
            output[index].append(translated.strip())
    return [" ".join(parts) for parts in output]
