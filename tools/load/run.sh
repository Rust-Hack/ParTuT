#!/bin/zsh
# Замер нагрузки на копии магазина — только локально, боевую базу не трогает.
#
#   tools/load/run.sh [итог.json]
#
# Пересоздаёт локальную базу partut_load (Postgres на 127.0.0.1:5433, как у
# тестов на Postgres), поднимает стенд tools/load/stand.py на порту 8793,
# прогоняет tools/load/run.py и останавливает стенд. Минуты полторы.
#
# «До и после» — тот же замер на старом коде из рабочей копии git:
#
#   git worktree add /tmp/partut-old <коммит>
#   LOAD_ROOT=/tmp/partut-old tools/load/run.sh до.json
#   tools/load/run.sh после.json
#
# Между прогонами — минута паузы: закрытые соединения держат порты ещё
# полминуты, и следующий прогон иначе споткнётся о чужой хвост.
#
# Ещё: ONLY=race,mix — только эти разделы; KEEPALIVE=0 — новое соединение на
# каждый запрос; PG_BIN — где лежат dropdb/createdb; LOAD_DB_PORT, LOAD_DB_USER.
set -e
ЗДЕСЬ=${0:A:h}
КОРЕНЬ=${ЗДЕСЬ:h:h}
ИТОГ=${1:-load_result.json}
export PATH="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}:$PATH"
export LOAD_DB_NAME=partut_load
ПОРТ_БАЗЫ=${LOAD_DB_PORT:-5433}
ХОЗЯИН=${LOAD_DB_USER:-partut}

lsof -ti tcp:8793 | xargs -r kill 2>/dev/null || true
sleep 1
dropdb -h 127.0.0.1 -p $ПОРТ_БАЗЫ -U $ХОЗЯИН --if-exists $LOAD_DB_NAME
createdb -h 127.0.0.1 -p $ПОРТ_БАЗЫ -U $ХОЗЯИН $LOAD_DB_NAME
ЖУРНАЛ=$(mktemp -t partut-load)
$КОРЕНЬ/venv/bin/python $ЗДЕСЬ/stand.py 8793 > $ЖУРНАЛ 2>&1 &
СТЕНД=$!
trap "kill $СТЕНД 2>/dev/null || true" EXIT
for i in $(seq 1 240); do curl -s -m 2 -o /dev/null http://127.0.0.1:8793/__load/meta && break; sleep 1; done
grep "товаров на точках" $ЖУРНАЛ || { echo "стенд не поднялся:"; tail -20 $ЖУРНАЛ; exit 1; }
$КОРЕНЬ/venv/bin/python $ЗДЕСЬ/run.py http://127.0.0.1:8793 $ИТОГ
echo "ошибок сервера в журнале стенда: $(grep -c 'ОШИБКА' $ЖУРНАЛ || true) (журнал: $ЖУРНАЛ)"
