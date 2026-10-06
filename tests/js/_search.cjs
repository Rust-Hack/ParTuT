// Настоящий код поиска из 01-core.js — для стендов, которым нужен поиск
// рабочих экранов (нашлось, нашласьМодель, ищетсяВ: варианты, «е» = «ё»).
// Подключение: vm.runInContext(require("./_search.cjs"), ctx).
const fs = require("node:fs");
const path = require("node:path");
const текст = fs.readFileSync(path.join(__dirname, "..", "..", "partut", "webapp", "app", "01-core.js"), "utf8");
function кусок(от, до) {
  const а = текст.indexOf(от), б = текст.indexOf(до, а);
  if (а < 0 || б < 0) throw new Error(`не нашёл в 01-core.js: ${от}`);
  return текст.slice(а, б);
}
module.exports = кусок("function flavorsOf(p)", "// Вкусы для фильтра") + "\n"
  + кусок("function searchText(p)", "function visibleProducts()");
