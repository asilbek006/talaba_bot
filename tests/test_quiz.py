from services import quiz


def test_uz_numeric():
    lines = [
        "1. Toshkent qaysi davlat poytaxti?",
        "A) Buxoro",
        "B) Toshkent",
        "C) Samarqand",
        "D) Qarshi",
        "To'g'ri javob: B",
    ]
    qs = quiz.parse_questions(lines)
    assert len(qs) == 1
    assert qs[0]["answer"] == 1
    assert len(qs[0]["options"]) == 4
    assert qs[0]["q"].startswith("Toshkent")


def test_savol_format():
    lines = [
        "Savol: 2+2 nechi?",
        "A) 3",
        "B) 4",
        "C) 5",
        "D) 6",
        "To'g'ri javob: B",
    ]
    qs = quiz.parse_questions(lines)
    assert len(qs) == 1
    assert qs[0]["q"].startswith("2+2")
    assert qs[0]["answer"] == 1


def test_ru_vopros_otvet():
    lines = [
        "Вопрос: Столица Узбекистана?",
        "A) Самарканд",
        "B) Ташкент",
        "C) Бухара",
        "D) Андижан",
        "Ответ: B",
    ]
    qs = quiz.parse_questions(lines)
    assert len(qs) == 1
    assert qs[0]["answer"] == 1
    assert qs[0]["q"].startswith("Столица")


def test_en_answer():
    lines = [
        "Question 1: Capital of France?",
        "A) Berlin",
        "B) Paris",
        "C) Madrid",
        "D) Rome",
        "Answer: B",
    ]
    qs = quiz.parse_questions(lines)
    assert len(qs) == 1
    assert qs[0]["answer"] == 1


def test_multiple_questions():
    lines = [
        "1. Savol 1",
        "A) a1",
        "B) b1",
        "C) c1",
        "D) d1",
        "Javob: A",
        "2. Savol 2",
        "A) a2",
        "B) b2",
        "C) c2",
        "D) d2",
        "To'g'ri javob: C",
    ]
    qs = quiz.parse_questions(lines)
    assert len(qs) == 2
    assert [q["answer"] for q in qs] == [0, 2]


def test_no_answer_missing():
    lines = ["1. Savol", "A) a", "B) b", "C) c", "D) d"]
    qs = quiz.parse_questions(lines)
    assert qs == []


def test_few_options_filtered():
    lines = ["1. Savol", "A) a", "Javob: A"]
    qs = quiz.parse_questions(lines)
    assert len(qs) == 0


def test_extract_docx(tmp_path):
    from docx import Document

    d = Document()
    d.add_paragraph("Savol:")
    d.add_paragraph("A) x")
    p = tmp_path / "q.docx"
    d.save(p)
    lines = quiz.extract_docx(p)
    assert lines == ["Savol:", "A) x"]


def test_txt_format(tmp_path):
    content = (
        "1. O'zbekiston mustaqilligi qachon e'lon qilingan?\n"
        "A) 1991-yil 31-avgust\n"
        "B) 1990-yil 1-sentabr\n"
        "C) 1992-yil 8-dekabr\n"
        "D) 1989-yil 21-oktabr\n"
        "To'g'ri javob: A\n\n"
        "2. Amir Temur qachon tavallud topgan?\n"
        "A) 1336-yil 9-aprel\n"
        "B) 1441-yil 9-fevral\n"
        "C) 1483-yil 14-fevral\n"
        "D) 1394-yil 22-mart\n"
        "Javob: A\n"
    )
    p = tmp_path / "test.txt"
    p.write_text(content, encoding="utf-8")
    qs = quiz.extract_questions_from_file(p)
    assert len(qs) == 2
    assert qs[0]["answer"] == 0
    assert qs[1]["answer"] == 0


def test_xlsx_format(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Savol", "A", "B", "C", "D", "To'g'ri javob"])
    ws.append(["O'zbekiston poytaxti qayer?", "Toshkent", "Samarqand", "Buxoro", "Xiva", "A"])
    ws.append(["2 + 2 nechiga teng?", "3", "4", "5", "6", "B"])
    p = tmp_path / "test.xlsx"
    wb.save(p)
    qs = quiz.extract_questions_from_file(p)
    assert len(qs) == 2
    assert qs[0]["answer"] == 0
    assert qs[1]["answer"] == 1
    assert qs[0]["q"].startswith("O'zbekiston")


def test_csv_format(tmp_path):
    content = (
        "Savol;A;B;C;D;Javob\n"
        "1. Alisher Navoiy qachon tug'ilgan?;1441;1483;1501;1336;A\n"
        "2. Bobur qachon tug'ilgan?;1441;1483;1501;1336;B\n"
    )
    p = tmp_path / "test.csv"
    p.write_text(content, encoding="utf-8")
    qs = quiz.extract_questions_from_file(p)
    assert len(qs) == 2
    assert qs[0]["answer"] == 0
    assert qs[1]["answer"] == 1


def test_cyrillic_options():
    lines = [
        "1. Савол матни?",
        "А) Биринчи вариант",
        "Б) Иккинчи вариант",
        "В) Учинчи вариант",
        "Г) Туртинчи вариант",
        "Жавоб: Б",
    ]
    qs = quiz.parse_questions(lines)
    assert len(qs) == 1
    assert qs[0]["answer"] == 1
    assert len(qs[0]["options"]) == 4


def test_text_direct_extraction():
    text = (
        "Savol: Quyosh tizimidagi eng katta sayyora qaysi?\n"
        "A) Mars\n"
        "B) Yupiter\n"
        "C) Venera\n"
        "D) Saturn\n"
        "To'g'ri javob: B\n\n"
        "Savol: Suvning kimyoviy formulasi qanday?\n"
        "A) H2O\n"
        "B) CO2\n"
        "C) NaCl\n"
        "D) O2\n"
        "To'g'ri javob: A\n"
    )
    qs = quiz.extract_questions_from_text(text)
    assert len(qs) == 2
    assert qs[0]["answer"] == 1
    assert qs[1]["answer"] == 0

