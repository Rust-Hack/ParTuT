"""
Прогон всех тестов перед деплоем:  python tests/run_all.py
Код выхода 0 = всё зелёное, 1 = есть падения (не деплоить).
"""
import sys

import test_order_lifecycle
import test_messaging
import test_stats
import test_delivery
import test_gzip

MODULES = [test_order_lifecycle, test_messaging, test_stats, test_delivery, test_gzip]


def main():
    all_fails = []
    for m in MODULES:
        all_fails += m.run()
    print("\n" + "=" * 40)
    if all_fails:
        print(f"❌ ПАДЕНИЙ: {len(all_fails)}")
        for f in all_fails:
            print("   •", f)
        return 1
    print("✅ ВСЕ ТЕСТЫ ПРОШЛИ — можно деплоить")
    return 0


if __name__ == "__main__":
    sys.exit(main())
