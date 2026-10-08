import json
import re
import time

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
            return json.loads(text[start:end + 1])
        raise


def _ask(prompt: str) -> dict:
    if not client:
        raise RuntimeError("GEMINI_API_KEY sozlanmagan (.env faylini tekshiring)")
    last = None
    for attempt in range(5):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.7,
                ),
            )
            return _extract_json(resp.text or "")
        except Exception as e:
            last = e
            msg = str(e)
            backoff = [3, 6, 12, 25][attempt] if attempt < 4 else 30
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                if attempt < 4:
                    time.sleep(backoff)
                    continue
                raise RuntimeError(
                    "AI xizmatining kunlik limiti tugadi. Keyinroq yana urinib "
                    f"ko'ring yoki yangi GEMINI_API_KEY kiriting. (429 RESOURCE_EXHAUSTED)"
                )
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last)[:300])


def _ask_validated(prompt: str, check) -> dict | list:
    data = _ask(prompt)
    if check(data):
        return data
    hint = ("\nMuhim: oldingi javobing strukturasi noto'g'ri edi. "
            "Faqat talab qilingan JSON shaklida, boshqa hech narsa qo'shmay qaytar.")
    data = _ask(prompt + hint)
    if not check(data):
        raise RuntimeError("AI javobini tahlil qilib bo'lmadi (format xatosi)")
    return data


def _topic_ok(topic: str, data) -> bool:
    words = [w for w in re.split(r"\W+", topic.lower()) if len(w) > 3]
    if not words:
        return True
    blob = " ".join(
        str(s.get("heading", "")) + " " + " ".join(str(p) for p in s.get("paragraphs", []))
        for s in (data.get("sections", []) if isinstance(data, dict) else []))
    blob += " " + (str(data.get("title", "")) if isinstance(data, dict) else "")
    return any(w in blob.lower() for w in words)


def _ref_ok(data) -> bool:
    return (isinstance(data, dict) and isinstance(data.get("sections"), list)
            and len(data["sections"]) > 0)


def gen_referat(lang: str, topic: str, pages: int) -> dict:
    content_pages = max(pages - 1, 1)
    words = content_pages * 300
    sections = min(14, max(4, pages // 2 + 1))
    words_per_section = max(150, words // sections)
    prompt = REFERAT_PROMPT.format(
        topic=topic, lang=LANG_NAMES.get(lang, lang),
        words=words, sections=sections, words_per_section=words_per_section)
    data = _ask_validated(prompt, _ref_ok)
    if not _topic_ok(topic, data):
        data = _ask_validated(prompt + f'\nMuhim: oldingi javobing "{topic}" mavzusiga mos emas edi. '
                                       'Aynan shu mavzu haqida qayta yoz, boshqa narsaga o\'tma!', _ref_ok)
    if _word_count(data) < int(words * 0.8):
        data = _ask_validated(prompt +
                              f"\nMuhim: hozircha {_word_count(data)} ta so'z bor, "
                              f"kamida {words} ta so'z bo'lishi kerak. Bo'limlarni to'ldirib, matnni uzaytir!",
                              _ref_ok)
    refs = data.get("references")
    if refs:
        literature = [r for r in map(str, refs) if r.strip()]
        if literature:
            label = {"uz": "ADABIYOTLAR", "ru": "ЛИТЕРАТУРА", "en": "REFERENCES"}.get(lang, "ADABIYOTLAR")
            data["sections"].append({"heading": label, "paragraphs": literature})
    data.setdefault("title", topic)
    data.setdefault("sections", [])
    return data


def _word_count(data: dict) -> int:
    return sum(len(str(p).split())
               for s in data.get("sections", []) for p in s.get("paragraphs", []))


def _ppt_ok(data) -> bool:
    return (isinstance(data, dict) and isinstance(data.get("slides"), list)
            and len(data["slides"]) > 0)


def gen_pptx(lang: str, topic: str, n: int) -> dict:
    prompt = PPTX_PROMPT.format(topic=topic, lang=LANG_NAMES.get(lang, lang), n=n)
    data = _ask_validated(prompt, _ppt_ok)
    if not _topic_ok(topic, data):
        data = _ask_validated(prompt + f'\nMuhim: oldingi javobing "{topic}" mavzusiga mos emas edi. '
                                       'Aynan shu mavzu haqida qayta yoz!', _ppt_ok)
    slides = data.get("slides", [])
    if len(slides) != n:
        data = _ask_validated(
            PPTX_PROMPT.format(topic=topic, lang=LANG_NAMES.get(lang, lang), n=n) +
            f"\nMuhim: oldingi javobda {len(slides)} ta slayd keldi, aynan {n} ta kerak edi.",
            _ppt_ok)
        slides = data.get("slides", [])
        if len(slides) > n:
            data["slides"] = slides[:n]
    data.setdefault("title", topic)
    data.setdefault("slides", [])
    return data


def _xls_ok(data) -> bool:
    return (isinstance(data, dict) and isinstance(data.get("headers"), list)
            and isinstance(data.get("rows"), list))


def gen_xlsx(lang: str, topic: str, rows: int) -> dict:
    prompt = XLSX_PROMPT.format(topic=topic, lang=LANG_NAMES.get(lang, lang), rows=rows)
    data = _ask_validated(prompt, _xls_ok)
    if len(data.get("rows", [])) != rows:
        data = _ask_validated(prompt +
                              f"\nMuhim: oldingi javobda {len(data.get('rows', []))} ta qator keldi, "
                              f"aynan {rows} ta qator kerak edi.", _xls_ok)
    data["rows"] = data.get("rows", [])[:rows]
    data.setdefault("title", topic)
    data.setdefault("headers", [])
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
    def ok(data) -> bool:
        return (isinstance(data, list) and len(data) > 0
                and all(isinstance(q, dict) and q.get("q")
                        and isinstance(q.get("options"), list)
                        and len(q["options"]) >= 2 for q in data))
    data = _ask_validated(TEST_PROMPT.format(topic=topic, lang=LANG_NAMES.get(lang, lang), n=n), ok)
    return data[:n]


def gen_translate(target: str, paragraphs: list[str]) -> list[str]:
    text = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(paragraphs))
    data = _ask(TRANSLATE_PROMPT.format(target=target, text=text))
    out = data.get("paragraphs") or []
    if len(out) < len(paragraphs):
        out = (out + [""] * len(paragraphs))[:len(paragraphs)]
    return out


def gen_rewrite(paragraphs: list[str]) -> list[str]:
    text = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(paragraphs))
    data = _ask(REWRITE_PROMPT.format(text=text))
    out = data.get("paragraphs") or []
    if len(out) < len(paragraphs):
        out = (out + [""] * len(paragraphs))[:len(paragraphs)]
    return out


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
    data = _ask_validated(PPTX_FROM_TEXT_PROMPT.format(lang=LANG_NAMES.get(lang, lang), n=n, text=text[:20000]), _ppt_ok)
    data.setdefault("title", "")
    data.setdefault("slides", [])
    if len(data.get("slides", [])) > n:
        data["slides"] = data["slides"][:n]
    return data
