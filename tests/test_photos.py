"""Картинки: качаются из Telegram один раз, потом живут в базе и в браузере.

Сеть не трогаем — Telegram подменён заглушками, которые считают скачивания.
"""
import io

from _common import db, client, server, Checker, as_admin

from partut.web import photos

from partut.integrations import tgsend

from partut import cache


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        pass


def run():
    as_admin()
    c = Checker("Фото: скачиваем из Telegram один раз")

    downloads = []          # сюда заглушка пишет каждое обращение к Telegram
    picture = b"\xff\xd8\xff" + b"vape" * 100

    real_get, real_get_file, real_bg = server.requests.get, tgsend.tg.get_file, tgsend.bg
    server.requests.get = lambda url, **kw: (downloads.append(url), _FakeResp(picture))[1]
    tgsend.tg.get_file = lambda fid: type("F", (), {"file_path": f"photos/{fid}.jpg"})()
    tgsend.bg = lambda fn, *a, **k: fn(*a, **k)   # запись в базу — сразу, без потока

    try:
        conn = db.connect(); conn.cursor().execute("DELETE FROM photo_blobs"); conn.commit(); conn.close()
        server._photo_cache.clear()
        server._file_path_cache.clear()
        db.update_field(db.add_product("minsk", "pods", "BlobPod", 10, 1), "photo", "photo1")

        r = client.get("/api/photo?file_id=photo1")
        c("первый запрос: 200", r.status_code == 200)
        c("отдали саму картинку", r.data == picture)
        c("сходили в Telegram один раз", len(downloads) == 1)
        c("ETag = file_id", r.headers.get("ETag") == '"photo1"')
        c("кэш надолго и immutable", "immutable" in r.headers.get("Cache-Control", ""))

        c("картинка сохранена в базу", db.get_photo_blob("photo1") is not None)
        c("байты в базе те же", db.get_photo_blob("photo1")[0] == picture)

        # Память процесса — как после перезапуска сервера на Render.
        server._photo_cache.clear()
        server._photo_cache_bytes = 0

        r2 = client.get("/api/photo?file_id=photo1")
        c("после перезапуска картинка отдана", r2.data == picture)
        c("в Telegram больше НЕ ходили", len(downloads) == 1)

        # Браузер уже знает эту картинку — тело гонять незачем.
        r3 = client.get("/api/photo?file_id=photo1", headers={"If-None-Match": '"photo1"'})
        c("повторный заход браузера → 304", r3.status_code == 304)
        c("304 без тела", r3.data == b"")

        # Чек об оплате — не фото товара: отдаём, но в базе не оставляем.
        r5 = client.get("/api/photo?file_id=receipt42")
        c("чек отдан", r5.data == picture)
        c("чек в базу НЕ сохранён", db.get_photo_blob("receipt42") is None)

        # Лимит памяти: превышение не должно ронять отдачу.
        server._photo_cache.clear()
        server._photo_cache_bytes = server.PHOTO_CACHE_MAX_BYTES
        r4 = client.get("/api/photo?file_id=photo1")
        c("при переполнении кэша картинка всё равно отдаётся", r4.data == picture)
        c("в память сверх лимита не положили", "photo1" not in server._photo_cache)

        # Кэш обязан ОБНОВЛЯТЬСЯ, а не застывать. Раньше при переполнении он
        # просто переставал принимать новое: в памяти навсегда оседали те
        # картинки, что попали туда первыми, а ходовой товар до конца жизни
        # процесса читался из базы каждый раз.
        cL = Checker("Кэш картинок вытесняет давние")
        server._photo_cache.clear()
        server._photo_cache_bytes = 0
        старый_предел = server.PHOTO_CACHE_MAX_BYTES
        server.PHOTO_CACHE_MAX_BYTES = 30            # места ровно на три картинки по 10 байт
        try:
            for имя in ("a", "b", "c"):
                server._photo_cache_put(имя, b"0123456789", "image/jpeg")
            cL("поместились все три", list(server._photo_cache) == ["a", "b", "c"])

            server._photo_cache_get("a")             # «a» снова в ходу
            server._photo_cache_put("d", b"0123456789", "image/jpeg")
            cL("новая картинка принята", "d" in server._photo_cache)
            cL("вытеснена самая давняя", "b" not in server._photo_cache)
            cL("недавно спрошенная осталась", "a" in server._photo_cache)
            cL("вес сошёлся с содержимым",
               server._photo_cache_bytes == 10 * len(server._photo_cache))
            cL("предел не превышен",
               server._photo_cache_bytes <= server.PHOTO_CACHE_MAX_BYTES)
        finally:
            server.PHOTO_CACHE_MAX_BYTES = старый_предел
            server._photo_cache.clear()
            server._photo_cache_bytes = 0
        лишние = cL.fails
        server._photo_cache_bytes = 0
    finally:
        server.requests.get, tgsend.tg.get_file, tgsend.bg = real_get, real_get_file, real_bg

    # --- Два размера ---
    c2 = Checker("Фото: для сетки берём копию поменьше")
    sizes = [type("P", (), {"file_id": "s90", "width": 90})(),
             type("P", (), {"file_id": "s800", "width": 800})(),
             type("P", (), {"file_id": "s1280", "width": 1280})()]
    full, grid = photos._pick_photo_sizes(sizes)
    c2("полная = самая большая", full == "s1280")
    c2("для сетки = не самая большая", grid == "s800")
    c2("пустой список не падает", photos._pick_photo_sizes([]) == (None, None))
    only = [type("P", (), {"file_id": "one", "width": 90})()]
    c2("если крупных нет — берём что есть", photos._pick_photo_sizes(only) == ("one", "one"))

    # Загрузка фото товара через админку сохраняет ОБА file_id.
    pid = db.add_product("minsk", "pods", "PhotoPod", 10, 1)
    r = client.post("/api/admin/photo",
                    data={"initData": "x", "id": str(pid), "file": (io.BytesIO(picture), "p.jpg")},
                    content_type="multipart/form-data")
    c2("загрузка фото прошла", (r.get_json() or {}).get("ok"))
    row = db.get_product(pid)
    c2("сохранена полноразмерная", row["photo"] == "fid")
    c2("сохранена копия для сетки", row["photo_thumb"] == "fid_m")

    cache.bust()
    p = next(x for x in client.get("/api/products").get_json() if x["id"] == pid)
    c2("в каталог уходит thumb_url", p["thumb_url"] == "/api/photo?file_id=fid_m")
    c2("карточка товара получает полную", p["photo_url"] == "/api/photo?file_id=fid")

    return c.fails + c2.fails + лишние


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
