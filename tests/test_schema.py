"""Схема базы: летопись изменений и запрет разрушительных правок.

Две разные вещи, и обе про один страх — выкатить версию и не суметь вернуться.

1. ПАМЯТЬ. Разовые переносы данных заводили себе по отметке в настройках,
   каждый свою, и вопрос «а на какой схеме эта база» ответа не имел вовсе —
   ни у боевой, ни у поднятой из резервной копии. Теперь есть
   schema_migrations, и она едет в копии вместе со всем остальным.

2. ДИСЦИПЛИНА. Сегодня схема правится только добавлением: тридцать
   ADD COLUMN и ни одного DROP или RENAME. Именно поэтому откат кода назад
   безопасен — старый код просто не смотрит на новые колонки. Но держится
   это на привычке, а не на устройстве: первая же разрушительная правка,
   написанная тем же способом, сломает откат молча. Здесь она перестанет
   быть молчаливой.
"""
import io
import os
import re

from _common import db, Checker

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Правки, после которых старый код перестаёт работать с новой базой.
РАЗРУШИТЕЛЬНЫЕ = [
    (r"\bDROP\s+TABLE\b", "DROP TABLE"),
    (r"\bDROP\s+COLUMN\b", "DROP COLUMN"),
    (r"\bRENAME\s+(TABLE|COLUMN|TO)\b", "RENAME"),
    (r"\bALTER\s+COLUMN\b", "ALTER COLUMN"),
]

# Где это допустимо: восстановление из копии сносит таблицы намеренно, и
# тестовый стенд чистит схему перед прогоном.
РАЗРЕШЕНО = ("import_tables", "DROP SCHEMA", "DROP TABLE IF EXISTS")

# Единственная правка типа, которую мы пропускаем: расширение денежной колонки
# до двойной точности. Любое число, представимое в четырёх байтах, точно
# представимо в восьми — потерять тут нечего.
#
# Оговорка узкая намеренно. Разрешить «ALTER COLUMN» целиком значило бы
# пропустить и сужение (double → real), и SET NOT NULL на колонке с пустыми
# значениями — а это уже потеря данных и падение при старте.
# \S+ вместо \w+ не случайность: имя колонки приезжает из f-строки и выглядит
# как {колонка} — фигурные скобки в \w+ не входят.
БЕЗОПАСНОЕ_РАСШИРЕНИЕ = r"\bALTER\s+COLUMN\s+\S+\s+TYPE\s+DOUBLE\s+PRECISION\b"


def run():
    c = Checker("Летопись изменений схемы")

    состояние = db.schema_version()
    имена = [и for и, _ in db.SCHEMA_MIGRATIONS]
    c(f"переносов заведено ({len(имена)})", len(имена) >= 3)
    c("все применены на этой базе" + ("" if not состояние["ждут"] else f": ждут {состояние['ждут']}"),
      not состояние["ждут"])
    c("видно, какой перенос последний", состояние["последняя"] == sorted(имена)[-1])
    c("у каждого записано время", all(r["applied_at"] for r in состояние["применено"]))

    # Порядок обязан читаться из имени: летопись сортируется по нему.
    без_номера = [и for и in имена if not re.match(r"^\d{4}-", и)]
    c("у каждого переноса номер в имени" + ("" if not без_номера else f": {без_номера}"),
      not без_номера)
    c("номера не повторяются", len({и[:4] for и in имена}) == len(имена))

    c2 = Checker("Перенос делается ровно один раз")
    # Второй заход не должен ни повторить работу, ни сломаться.
    было = len(db.schema_version()["применено"])
    сделал = db._migrate(имена[0], lambda: (_ for _ in ()).throw(
        AssertionError("перенос запустился второй раз")))
    c2("повторный вызов не запускает работу", сделал is False)
    c2("и не плодит записей", len(db.schema_version()["применено"]) == было)

    # Незарегистрированный перенос — это шаг, заведённый втихую.
    try:
        db._migrate("9999-не-записан-в-список", lambda: None)
        c2("незарегистрированный перенос отвергнут", False)
    except RuntimeError:
        c2("незарегистрированный перенос отвергнут", True)

    # Упавший перенос не должен считаться сделанным: иначе данные остались бы
    # наполовину старыми, и это никогда бы не всплыло.
    c3 = Checker("Упавший перенос не считается сделанным")
    conn = db.connect(); cur = conn.cursor()
    cur.execute(db._q("DELETE FROM schema_migrations WHERE name = %s"), (имена[1],))
    conn.commit(); conn.close()
    старая = db.get_setting(dict(db.SCHEMA_MIGRATIONS)[имена[1]])
    db.set_setting(dict(db.SCHEMA_MIGRATIONS)[имена[1]], "")
    try:
        db._migrate(имена[1], lambda: (_ for _ in ()).throw(RuntimeError("не вышло")))
        c3("падение проброшено наверх", False)
    except RuntimeError:
        c3("падение проброшено наверх", True)
    c3("запись о переносе снята", имена[1] in db.schema_version()["ждут"])
    # возвращаем базу в порядок
    db.set_setting(dict(db.SCHEMA_MIGRATIONS)[имена[1]], старая or "1")
    db._migrate(имена[1], lambda: None)
    c3("база вернулась в исходное", not db.schema_version()["ждут"])

    c4 = Checker("Схема правится только добавлением")
    # Все файлы пакета базы, а не список имён: новый кусок db попадёт под
    # проверку сам. Раньше здесь искались файлы «db*.py» в корне — после
    # переезда в partut/db/ поиск нашёл бы ноль, и проверка прошла бы вхолостую.
    ПАПКА_БАЗЫ = os.path.join(КОРЕНЬ, "partut", "db")
    файлы_базы = sorted(и for и in os.listdir(ПАПКА_БАЗЫ) if и.endswith(".py"))
    c4(f"файлы базы найдены ({len(файлы_базы)})", len(файлы_базы) >= 5)
    for файл in файлы_базы:
        текст = io.open(os.path.join(ПАПКА_БАЗЫ, файл), encoding="utf-8").read()
        находки = []
        for строка in текст.splitlines():
            if any(р in строка for р in РАЗРЕШЕНО) or строка.lstrip().startswith("#"):
                continue
            if re.search(БЕЗОПАСНОЕ_РАСШИРЕНИЕ, строка, re.I):
                continue
            for образец, имя in РАЗРУШИТЕЛЬНЫЕ:
                if re.search(образец, строка, re.I):
                    находки.append(f"{имя}: {строка.strip()[:60]}")
        c4(f"{файл[:-3]} без разрушительных правок"
           + ("" if not находки else f": {находки}"), not находки)

    c5 = Checker("Летопись едет в резервной копии")
    копия = db.export_tables()
    c5("schema_migrations попала в копию", "schema_migrations" in копия)
    c5("и не пустая", len(копия.get("schema_migrations", [])) >= 3)

    return c.fails + c2.fails + c3.fails + c4.fails + c5.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)


def run_upgrade_existing():
    """Что случится с БОЕВОЙ базой при первом деплое с летописью.

    Там уже стоят старые отметки в настройках, а таблицы schema_migrations
    ещё нет. Летопись обязана записать переносы сделанными, НЕ выполняя их:
    повторный перенос прогресса колеса умножил бы накопленное покупателями
    на двадцать, и вернуть это было бы нечем.

    Случай наступает ровно один раз и переиграть его нельзя — поэтому он
    проверяется здесь, до деплоя, а не после.
    """
    import tempfile

    c = Checker("Первый деплой с летописью на живую базу")
    if db.USE_PG:
        # Второй пустой базы под рукой нет; на SQLite сценарий тот же.
        c("на Postgres не проверяется — здесь пропущено", True)
        return c.fails

    прежний = db.SQLITE_FILE
    db.SQLITE_FILE = tempfile.mktemp(suffix=".db")
    try:
        db.init_db()

        # База «как до летописи»: отметки есть, летописи нет.
        conn = db.connect(); cur = conn.cursor()
        cur.execute("DROP TABLE schema_migrations")
        conn.commit(); conn.close()
        for _, метка in db.SCHEMA_MIGRATIONS:
            db.set_setting(метка, "1")

        db.ensure_user(4242)
        conn = db.connect(); cur = conn.cursor()
        cur.execute(db._q("UPDATE users SET wheel_progress = %s WHERE user_id = %s"), (60, 4242))
        conn.commit(); conn.close()
        было = db.get_wheel(4242)["progress"]

        db.init_db()                    # ← сам деплой

        c("накопленный прогресс не тронут", db.get_wheel(4242)["progress"] == было)
        c("все переносы записаны в летопись", not db.schema_version()["ждут"])
        c("записей ровно столько, сколько переносов",
          len(db.schema_version()["применено"]) == len(db.SCHEMA_MIGRATIONS))

        db.init_db()                    # и следующий запуск тоже ничего не делает
        c("повторный запуск ничего не меняет",
          db.get_wheel(4242)["progress"] == было
          and len(db.schema_version()["применено"]) == len(db.SCHEMA_MIGRATIONS))
    finally:
        try:
            os.unlink(db.SQLITE_FILE)
        except OSError:
            pass
        db.SQLITE_FILE = прежний
    return c.fails
