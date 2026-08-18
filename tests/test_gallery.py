"""Несколько фото у модели.

Одна картинка не показывает ни размер, ни комплект, ни экран — покупатель
додумывает, а продавец отвечает на одни и те же вопросы в чате.

Галерея принадлежит МОДЕЛИ, а не наличию на точке: на всех точках это одна
и та же коробка, и держать три копии одних снимков незачем.
"""
from _common import db, client, server, Checker, as_admin, deny_admin


def _clean():
    conn = db.connect(); cur = conn.cursor()
    cur.execute("DELETE FROM product_photos")
    cur.execute("DELETE FROM products")
    conn.commit(); conn.close()
    server._cache_bust()


def _upload(pid, path="/api/admin/photo/add", model_id=None):
    """Загрузка фото как из браузера — multipart с файлом."""
    data = {"initData": "x", "file": (__import__("io").BytesIO(b"jpegdata"), "p.jpg")}
    if model_id is None:
        data["id"] = str(pid)
    else:
        data["model_id"] = str(model_id)
    return client.post(path, data=data, content_type="multipart/form-data")


def _product(pid):
    server._cache_bust()
    return next(p for p in client.get("/api/products").get_json() if p["id"] == pid)


def run():
    c = Checker("Галерея товара")
    _clean()
    as_admin()

    mid = db.add_model("podsystem", "ГалереяПод")
    pid = db.add_product_from_model(mid, "Минск", 30.0, stock=3)

    # --- Товар без фото ---
    c("без фото галерея пустая", _product(pid)["photos"] == [])

    # --- Главное фото модели попадает в галерею первым ---
    # Главное фото — тоже свойство модели: на всех точках товар выглядит одинаково.
    r = _upload(pid, "/api/admin/model/photo", model_id=mid)
    c("главное фото загружено", (r.get_json() or {}).get("ok"))
    p = _product(pid)
    c("главное фото первое в галерее", len(p["photos"]) == 1 and p["photos"][0]["id"] == 0)
    c("старое поле photo_url на месте", bool(p["photo_url"]))
    c("в каталоге по-прежнему мелкая копия", p["thumb_url"] != p["photo_url"])

    # --- Дополнительные фото ---
    r = _upload(pid, model_id=mid)
    d = r.get_json()
    c("доп. фото добавлено", d.get("ok") and d.get("photo_id"))
    _upload(pid, model_id=mid)
    p = _product(pid)
    c("в галерее три картинки", len(p["photos"]) == 3)
    c("главное осталось первым", p["photos"][0]["id"] == 0)
    c("у доп. фото свой id", p["photos"][1]["id"] > 0)
    c("замена главного не трогает галерею",
      (_upload(pid, "/api/admin/model/photo", model_id=mid).get_json() or {}).get("ok")
      and len(_product(pid)["photos"]) == 3)

    # --- Предел ---
    for _ in range(5):
        _upload(pid, model_id=mid)
    c("больше предела не добавить", len(db.model_photos(mid)) == db.MAX_EXTRA_PHOTOS)
    over = _upload(pid, model_id=mid)
    c("лишнее фото отклонено с понятной причиной",
      over.status_code == 400 and over.get_json()["error"] == "too_many")
    c("в галерее главное + предел", len(_product(pid)["photos"]) == db.MAX_EXTRA_PHOTOS + 1)

    # --- Удаление ---
    photo_id = db.model_photos(mid)[0]["id"]
    r = client.post("/api/admin/photo/delete", json={"initData": "x", "photo_id": photo_id})
    c("фото убрано", r.get_json().get("deleted"))
    c("в галерее стало меньше", len(_product(pid)["photos"]) == db.MAX_EXTRA_PHOTOS)
    c("удалённого id больше нет", all(g["id"] != photo_id for g in _product(pid)["photos"]))
    c("главное фото так не удалить",
      client.post("/api/admin/photo/delete", json={"initData": "x", "photo_id": 0}).status_code == 400)

    # --- Картинки галереи храним у себя ---
    # Иначе после перезапуска сервера они качались бы из Telegram заново.
    fid = db.model_photos(mid)[0]["file_id"]
    c("доп. фото считается фото товара", db.is_product_photo(fid))

    # --- Мусор и права ---
    c("фото несуществующей модели не добавить", _upload(pid, model_id=999999).status_code == 404)
    c("без файла отказ",
      client.post("/api/admin/photo/add", data={"initData": "x", "model_id": str(mid)},
                  content_type="multipart/form-data").status_code == 400)
    deny_admin()
    c("посторонний фото не добавит", _upload(pid, model_id=mid).status_code == 403)
    c("посторонний фото не удалит",
      client.post("/api/admin/photo/delete", json={"initData": "x", "photo_id": 1}).status_code == 403)
    as_admin()

    # --- Галерея одна на все точки ---
    c2 = Checker("Галерея принадлежит модели")
    pid2 = db.add_product_from_model(mid, "Туров", 32.0, stock=2)
    server._cache_bust()
    a, b = _product(pid), _product(pid2)
    c2("вторая точка видит те же фото", len(a["photos"]) == len(b["photos"]) > 1)
    c2("и это ровно главное фото модели плюс её галерея", len(db.model_photos(mid)) + 1 == len(b["photos"]))
    db.delete_product(pid2)
    c2("удаление наличия на точке фото не трогает", len(db.model_photos(mid)) > 0)

    _clean()
    return c.fails + c2.fails


if __name__ == "__main__":
    import sys
    sys.exit(1 if run() else 0)
